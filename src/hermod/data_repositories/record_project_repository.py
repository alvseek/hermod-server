"""`ProjectRepository` over Valaskjalf's record tables.

The store is not Hermod's. A project-context entry is a `knowledge` record carrying a `project`:
a **shared** entry is a `shared_record` row, a **private** entry is a `memory_record` row whose
`agent_id` carries the owner the old table had no column for, and the orientation map is a
`shared_record` row titled `orientation-map` (ADR-027).

Four things follow from not owning the store, and each one explains a line below:

- **No DDL.** The tables have one owner, the memory store's migration runner, so this class never
  creates or alters anything. A missing table or column surfaces on the first use, which is
  deliberate: a second runner would be a second opinion about the shape of a shared table.
- **A tenant on every row.** `user_id` is not optional, so the repository is constructed with the
  resolved tenant and is never process-wide. The tenant comes from the caller's verified token;
  the agent comes from the call. They are orthogonal, and both are needed.
- **The agent is required for the private scope.** `memory_record` is agent-scoped, so a private
  read without one would answer with every agent's rows at once. Asking for it is what keeps a
  private listing from being an aggregate.
- **`title` is the theme.** The record pair has no column for a context theme, and the theme is
  what the markdown backend addressed an entry by, so it rides in `title`, and an entry's
  identity is exactly what a `uuid5` over its parts produces.

**PostgreSQL only, deliberately.** The store has no local shape: a verified token is what names the
tenant a call may touch, so with no issuer there is nothing to resolve a tenant from and every store
op raises rather than answering under a guessed account. (The *content* surface — procedures and
templates — does run unauthenticated on loopback; only the store needs a token.) That means the only
store Hermod ever reads is the deployed one, and SQLite was carried here to let the suite run
without a service before being removed for the reason it was dubious: a second implementation of the
contract that nothing deploys can drift with no consequence, and CI supplies PostgreSQL in a few
lines instead. The class is smaller for it.
"""

from __future__ import annotations

import json
import uuid as _uuidlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from hermod.data_entities.awakening import CodingPattern, Episode, EpisodeIndexEntry
from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap

#: The type a project row carries. Munnin's records of this type that also carry a `project` are
#: project context; the ones that do not are its own general knowledge (ADR-027).
RECORD_TYPE = "knowledge"
#: The map's natural key inside `shared_record`. A project has one map, so the title is the key,
#: and a second create is refused by the uuid it derives rather than by a constraint.
MAP_TITLE = "orientation-map"
#: Munnin's episodic record type. Hermod reads it for the coding awakening's carry-forward and
#: never writes or retires it (ADR-029).
EPISODE_TYPE = "episode"
#: The coding lane's record type — the overlay's own lane, shared and project-less, which is what
#: keeps coding patterns out of a chat agent's awakening.
CODING_REASONING_TYPE = "coding_reasoning"

#: The namespace a project row's uuid is derived in, so the same entry always lands on the same
#: row: a re-import upserts rather than duplicating, and a lookup needs no scan.
_UUID_NS = _uuidlib.UUID("9f4c1d0a-2b7e-5c31-8a6d-1e0f3a5b7c9d")

# One source of truth for the columns each table carries, and for the order a row is read in.
_SHARED_INSERT = (
    "uuid", "user_id", "record_type", "project", "title", "tags",
    "created_date", "modified_date", "archived_date", "deleted_date", "full_content",
    "content_summary",
)
_MEMORY_INSERT = (
    "uuid", "user_id", "agent_id", "record_type", "project", "title", "tags",
    "created_date", "modified_date", "archived_date", "deleted_date", "full_content",
    "content_summary",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MissingAgentError(ValueError):
    """A private write named an agent this account has no row for.

    A ``ValueError`` like the duplicate refusal, because both are caller mistakes and both surface
    the same way through the served tools — but a *distinct* type, because the two mean opposite
    things to a caller that counts outcomes: one says "already there", the other says "not written".
    The importer relies on that difference, and collapsing them made a missing agent read as an
    entry that was already present.
    """


def entry_uuid(
    *,
    user_id: str,
    project: str,
    scope: ContextScope,
    theme: str,
    agent: str | None = None,
) -> str:
    """The stable identity of an entry — the key the store is written under.

    The owner is part of it for the private scope, because `memory_record` carries `agent_id`:
    leaving it out would give two agents' rows for one theme a single identity, which is the
    collision this move exists to remove.
    """
    return str(
        _uuidlib.uuid5(_UUID_NS, f"{user_id}|{project}|{scope.value}|{agent or ''}|{theme}")
    )


def map_uuid(*, user_id: str, project: str) -> str:
    """The stable identity of a project's map."""
    return str(_uuidlib.uuid5(_UUID_NS, f"{user_id}|{project}|{MAP_TITLE}"))


class RecordProjectRepository:
    """Implements ``ProjectRepository`` (structural) over Valaskjalf's records."""

    def __init__(self, *, user_id: str, db_url: str) -> None:
        self._user_id = user_id
        self._db_url = db_url

    # --- connection ---------------------------------------------------------------

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._db_url, row_factory=dict_row)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _require_agent(self, agent: str | None) -> str:
        if not agent:
            raise ValueError(
                "the private scope is agent-scoped: pass agent=<domain>, or use "
                "scope='shared' for the cross-agent layer"
            )
        return agent

    # --- project context: reads ---------------------------------------------------

    def list_context_entries(
        self, *, project: str, scope: ContextScope | None = None, agent: str | None = None
    ) -> Sequence[ContextEntry]:
        """A project's live entries, shared and private, in theme order within each scope.

        The private half needs an ``agent``. Without one the only honest answer would be "every
        agent's private rows", which is the aggregate this scope exists to avoid, so it is refused
        rather than answered.
        """
        found: list[ContextEntry] = []
        if scope in (None, ContextScope.shared):
            found += self._select(project=project, scope=ContextScope.shared, agent=None)
        if scope in (None, ContextScope.private):
            found += self._select(
                project=project, scope=ContextScope.private, agent=self._require_agent(agent)
            )
        return found

    def get_context_entry(
        self, *, project: str, scope: ContextScope, theme: str, agent: str | None = None
    ) -> ContextEntry | None:
        owner = self._require_agent(agent) if scope is ContextScope.private else None
        row = self._select_one(project=project, scope=scope, theme=theme, agent=owner)
        return _row_to_entry(row) if row else None

    # --- project context: writes --------------------------------------------------

    def create_context_entry(
        self, entry: ContextEntry, *, agent: str | None = None
    ) -> ContextEntry:
        """Insert one entry, refusing to touch an existing live one.

        The uuid is derived from the entry's identity, so a create over a theme that already
        exists is reported as the caller mistake it is, and a create over a *tombstoned* theme
        revives that row instead, because the identity is the same one: a theme keeps one row, and
        one history.
        """
        owner = self._require_agent(agent) if entry.scope is ContextScope.private else None
        identity = entry_uuid(
            user_id=self._user_id,
            project=entry.project,
            scope=entry.scope,
            theme=entry.theme,
            agent=owner,
        )
        created = entry.created_date or _now()
        modified = entry.modified_date or created
        private = entry.scope is ContextScope.private
        table = "memory_record" if private else "shared_record"
        tags = json.dumps(entry.tags or [])
        # The two column lists differ by exactly the owner, so the value tuples do too. Written
        # out rather than spliced, because the version that removed `agent_id` by string surgery
        # was one rename away from silent drift.
        columns = _MEMORY_INSERT if private else _SHARED_INSERT
        values: tuple[object, ...] = (
            (identity, self._user_id, owner, RECORD_TYPE, entry.project, entry.theme, tags,
             created, modified, None, None, entry.full_content, entry.description)
            if private
            else (identity, self._user_id, RECORD_TYPE, entry.project, entry.theme, tags,
                  created, modified, None, None, entry.full_content, entry.description)
        )
        with self._conn() as conn, conn.cursor() as cur:
            # Existence first, so the ordinary paths never raise: a duplicate raises nothing, and
            # a refused INSERT would abort the transaction and take the reads with it.
            cur.execute(
                f"SELECT deleted_date FROM {table} WHERE uuid = %s AND user_id = %s",
                (identity, self._user_id),
            )
            existing = cur.fetchone()
            if existing is not None:
                if existing["deleted_date"] is None:
                    self._refuse_duplicate(entry)
                # A tombstone holds the same identity, so a create over it revives that row.
                cur.execute(
                    f"UPDATE {table} SET full_content = %s, content_summary = %s, tags = %s, "
                    "modified_date = %s, deleted_date = NULL WHERE uuid = %s",
                    (
                        entry.full_content,
                        entry.description,
                        json.dumps(entry.tags or []),
                        modified,
                        identity,
                    ),
                )
            else:
                try:
                    cur.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) "
                        f"VALUES ({', '.join(['%s'] * len(columns))})",
                        values,
                    )
                except psycopg.errors.ForeignKeyViolation as exc:
                    self._refuse_missing_agent(owner, exc)
                except psycopg.errors.UniqueViolation as exc:
                    self._refuse_duplicate(entry, exc)
            cur.execute(f"SELECT * FROM {table} WHERE uuid = %s", (identity,))
            row = cur.fetchone()
        return _row_to_entry(row)

    def _refuse_duplicate(self, entry: ContextEntry, exc: Exception | None = None) -> None:
        raise ValueError(
            f"a {entry.scope.value} context entry for {entry.project!r} already exists under "
            f"theme {entry.theme!r}; update it instead of creating it, or delete it first"
        ) from exc

    def _refuse_missing_agent(self, agent: str | None, exc: Exception | None = None) -> None:
        raise MissingAgentError(
            f"no agent {agent!r} exists for this account, so it cannot own a private context "
            "entry yet. Create the agent in the memory store first."
        ) from exc

    def update_context_entry(
        self, entry: ContextEntry, *, agent: str | None = None
    ) -> ContextEntry:
        """Replace a live entry's body, summary and tags; ``created_date`` is preserved."""
        owner = self._require_agent(agent) if entry.scope is ContextScope.private else None
        identity = entry_uuid(
            user_id=self._user_id,
            project=entry.project,
            scope=entry.scope,
            theme=entry.theme,
            agent=owner,
        )
        table = "memory_record" if entry.scope is ContextScope.private else "shared_record"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET full_content = %s, content_summary = %s, tags = %s, "
                "modified_date = %s WHERE uuid = %s AND user_id = %s AND deleted_date IS NULL",
                (
                    entry.full_content,
                    entry.description,
                    json.dumps(entry.tags or []),
                    _now(),
                    identity,
                    self._user_id,
                ),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"no live {entry.scope.value} context entry {entry.theme!r} in project "
                    f"{entry.project!r}"
                )
            cur.execute(f"SELECT * FROM {table} WHERE uuid = %s", (identity,))
            row = cur.fetchone()
        return _row_to_entry(row)

    def move_context_entry(
        self,
        *,
        project: str,
        theme: str,
        from_scope: ContextScope,
        to_scope: ContextScope,
        agent: str | None = None,
    ) -> ContextEntry:
        """Move an entry between scopes: create the row in one table, tombstone it in the other.

        Two steps rather than one UPDATE, because the scope is *which table* the row lives in —
        and they are two transactions, since every op opens its own connection. A failure between
        them therefore leaves the entry live in **both** scopes and raises nothing: the exposure is
        the same one the markdown backend's copy-then-delete carries, and it is inherited rather
        than reported. A caller that needs to know should read both scopes after a move.
        """
        if from_scope is to_scope:
            raise ValueError(f"move: {from_scope.value} to itself is not a move")
        source_owner = self._require_agent(agent) if from_scope is ContextScope.private else None
        target_owner = self._require_agent(agent) if to_scope is ContextScope.private else None
        if (
            self.get_context_entry(
                project=project, scope=to_scope, theme=theme, agent=target_owner
            )
            is not None
        ):
            raise ValueError(
                f"cannot move {theme!r} to {to_scope.value}: a live {to_scope.value} entry "
                f"already holds that theme in {project!r}"
            )
        row = self._select_one(project=project, scope=from_scope, theme=theme, agent=source_owner)
        if row is None:
            raise LookupError(
                f"no live {from_scope.value} context entry {theme!r} in project {project!r}"
            )
        moved = _row_to_entry(row)
        created = self.create_context_entry(
            ContextEntry(
                project=project,
                scope=to_scope,
                theme=theme,
                full_content=moved.full_content,
                description=moved.description,
                tags=list(moved.tags),
                created_date=moved.created_date,
                modified_date=_now(),
            ),
            agent=target_owner,
        )
        self.delete_context_entry(
            project=project, scope=from_scope, theme=theme, agent=source_owner
        )
        return created

    def delete_context_entry(
        self, *, project: str, scope: ContextScope, theme: str, agent: str | None = None
    ) -> None:
        """Tombstone one entry. Idempotent, and a theme never present at all is a ``LookupError``.

        Idempotence has a second meaning here: the row is tombstoned rather than deleted, so the
        uuid stays and a later create over the same theme revives it rather than adding a second
        row for one identity.
        """
        owner = self._require_agent(agent) if scope is ContextScope.private else None
        identity = entry_uuid(
            user_id=self._user_id, project=project, scope=scope, theme=theme, agent=owner
        )
        table = "memory_record" if scope is ContextScope.private else "shared_record"
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET deleted_date = COALESCE(deleted_date, %s) "
                "WHERE uuid = %s AND user_id = %s",
                (_now(), identity, self._user_id),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"no {scope.value} context entry {theme!r} in project {project!r}"
                )

    # --- the orientation map ------------------------------------------------------

    def map_exists(self, *, project: str) -> bool:
        row = self._select_one(project=project, scope=ContextScope.shared, theme=MAP_TITLE)
        return row is not None

    def get_map(self, *, project: str) -> OrientationMap | None:
        row = self._select_one(project=project, scope=ContextScope.shared, theme=MAP_TITLE)
        return _row_to_map(row) if row else None

    def create_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Insert the project's map. A second create is refused because the key is derived."""
        identity = map_uuid(user_id=self._user_id, project=orientation_map.project)
        created = orientation_map.created_date or _now()
        modified = orientation_map.modified_date or created
        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    f"INSERT INTO shared_record ({', '.join(_SHARED_INSERT)}) "
                    f"VALUES ({', '.join(['%s'] * len(_SHARED_INSERT))})",
                    (
                        identity, self._user_id, RECORD_TYPE, orientation_map.project, MAP_TITLE,
                        json.dumps([]), created, modified, None, None,
                        orientation_map.full_content, orientation_map.description,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(
                    f"project {orientation_map.project!r} already has an orientation map; "
                    "write it instead of creating it"
                ) from exc
            cur.execute("SELECT * FROM shared_record WHERE uuid = %s", (identity,))
            row = cur.fetchone()
        return _row_to_map(row)

    def write_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Rewrite the map wholesale. Raises ``LookupError`` when there is none to write."""
        identity = map_uuid(user_id=self._user_id, project=orientation_map.project)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE shared_record SET full_content = %s, content_summary = %s, "
                "modified_date = %s WHERE uuid = %s AND user_id = %s AND deleted_date IS NULL",
                (
                    orientation_map.full_content,
                    orientation_map.description,
                    _now(),
                    identity,
                    self._user_id,
                ),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"project {orientation_map.project!r} has no orientation map to write"
                )
            cur.execute("SELECT * FROM shared_record WHERE uuid = %s", (identity,))
            row = cur.fetchone()
        return _row_to_map(row)

    # --- the memory rows the coding awakening reads --------------------------------

    def newest_episode_per_project(
        self, *, agent: str, projects: Sequence[str]
    ) -> Sequence[Episode]:
        """One newest live episode per project in ``projects``, in the set's own order.

        One query rather than one per project: ``DISTINCT ON (project)`` picks each project's
        newest row and the outer sort restores the caller's order. A project with no live
        episode is absent rather than empty, so a caller can tell "no episode" from "an
        episode with no body".

        **Both lifecycle columns are filtered here, and ``_select`` filters only one of
        them.** ``_select``'s note that "nothing archives today" is a fact about context rows;
        Munnin archives *episodes*, so an archived one is retired context and must not be
        carried forward. These are therefore the same filters Munnin's own episode read
        applies, and that parity is what keeps a deleted or archived episode out of an
        awakening.
        """
        if not projects:
            return []
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (project) * FROM memory_record "
                "WHERE user_id = %s AND agent_id = %s AND record_type = %s "
                "AND project = ANY(%s) AND deleted_date IS NULL AND archived_date IS NULL "
                "ORDER BY project, created_date DESC, id DESC",
                (self._user_id, agent, EPISODE_TYPE, list(projects)),
            )
            rows = cur.fetchall()
        newest = {row["project"]: _row_to_episode(row) for row in rows}
        return [newest[project] for project in projects if project in newest]

    def coding_lane(self) -> Sequence[CodingPattern]:
        """The whole lane, in title order.

        Shared memory, so it is read under the tenant and never under an agent: the lane has
        no owner, and asking for one would be a scoping mistake rather than a safety measure.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM shared_record WHERE user_id = %s AND record_type = %s "
                "AND deleted_date IS NULL AND archived_date IS NULL ORDER BY title",
                (self._user_id, CODING_REASONING_TYPE),
            )
            rows = cur.fetchall()
        return [_row_to_pattern(row) for row in rows]

    def episode_index(
        self, *, agent: str, projects: Sequence[str]
    ) -> Sequence[EpisodeIndexEntry]:
        """Every live episode of the set, newest first, without bodies.

        The columns are named because that *is* the read: a body a caller will not use is
        weight this index exists to avoid carrying. Same lifecycle filters as the newest-body
        read next to it, for the same reason.
        """
        if not projects:
            return []
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT uuid, title, content_summary, project, tags, created_date, modified_date "
                "FROM memory_record WHERE user_id = %s AND agent_id = %s AND record_type = %s "
                "AND project = ANY(%s) AND deleted_date IS NULL AND archived_date IS NULL "
                "ORDER BY created_date DESC, id DESC",
                (self._user_id, agent, EPISODE_TYPE, list(projects)),
            )
            rows = cur.fetchall()
        return [_row_to_index_entry(row) for row in rows]

    # --- the two selects everything reads through ---------------------------------

    def _select(
        self, *, project: str, scope: ContextScope, agent: str | None
    ) -> list[ContextEntry]:
        table = "memory_record" if scope is ContextScope.private else "shared_record"
        # `deleted_date` is the only lifecycle this backend writes or reads. Hermod has no archive
        # op, so it takes no view on `archived_date`: a row archived through the memory store's own
        # tool stays visible here, where the memory store would hide it. The two servers disagree
        # about what "live" means, in one direction, on rows nothing archives today.
        where = [
            "user_id = %s",
            "record_type = %s",
            "project = %s",
            "deleted_date IS NULL",
            "title <> %s",
        ]
        params: list[object] = [self._user_id, RECORD_TYPE, project, MAP_TITLE]
        if agent is not None:
            where.append("agent_id = %s")
            params.append(agent)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY title", params
            )
            rows = cur.fetchall()
        return [_row_to_entry(r) for r in rows]

    def _select_one(
        self, *, project: str, scope: ContextScope, theme: str, agent: str | None = None
    ) -> dict | None:
        table = "memory_record" if scope is ContextScope.private else "shared_record"
        where = [
            "user_id = %s",
            "record_type = %s",
            "project = %s",
            "title = %s",
            "deleted_date IS NULL",
        ]
        params: list[object] = [self._user_id, RECORD_TYPE, project, theme]
        if scope is ContextScope.private:
            where.append("agent_id = %s")
            params.append(self._require_agent(agent))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {table} WHERE {' AND '.join(where)}", params)
            return cur.fetchone()


def _row_to_entry(row: dict) -> ContextEntry:
    return ContextEntry(
        id=row["id"],
        project=row["project"],
        scope=ContextScope.private if "agent_id" in row else ContextScope.shared,
        theme=row["title"],
        description=row["content_summary"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        created_date=row["created_date"],
        modified_date=row["modified_date"],
        deleted_date=row["deleted_date"],
        full_content=row["full_content"],
    )


def _row_to_map(row: dict) -> OrientationMap:
    return OrientationMap(
        id=row["id"],
        project=row["project"],
        description=row["content_summary"],
        created_date=row["created_date"],
        modified_date=row["modified_date"],
        full_content=row["full_content"],
    )


def _row_to_episode(row: dict) -> Episode:
    return Episode(
        id=row["id"],
        agent=row["agent_id"],
        project=row["project"],
        title=row["title"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        created_date=row["created_date"],
        modified_date=row["modified_date"],
        full_content=row["full_content"],
    )


def _row_to_pattern(row: dict) -> CodingPattern:
    return CodingPattern(
        id=row["id"],
        title=row["title"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        created_date=row["created_date"],
        modified_date=row["modified_date"],
        full_content=row["full_content"],
    )


def _row_to_index_entry(row: dict) -> EpisodeIndexEntry:
    return EpisodeIndexEntry(
        uuid=row["uuid"],
        title=row["title"],
        content_summary=row["content_summary"],
        project=row["project"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        created_date=row["created_date"],
        modified_date=row["modified_date"],
    )
