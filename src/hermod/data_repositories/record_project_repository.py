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
- **`title` is the theme, and a name only.** The record pair has no column for a context theme,
  so the theme rides in `title` as the markdown backend had it. It is no longer an identity: the
  store assigns the row's `uuid` at insert, and every op that acts on one row takes that uuid.
  The theme is what a listing shows and what an agent compares when it decides between creating
  and updating, nothing more.

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
#: and the create refuses a second one by looking for that row rather than by a constraint:
#: uniqueness in this store is the uuid's alone.
MAP_TITLE = "orientation-map"
#: Munnin's episodic record type. Hermod reads it for the coding awakening's carry-forward and
#: never writes or retires it (ADR-029).
EPISODE_TYPE = "episode"
#: The coding lane's record type — the overlay's own lane, shared and project-less, which is what
#: keeps coding patterns out of a chat agent's awakening.
CODING_REASONING_TYPE = "coding_reasoning"

#: The table each layer's rows live in. The layer is the table, which is why a direction change
#: is a copy plus a tombstone rather than one UPDATE.
_SHARED_TABLE = "shared_record"
_AGENT_TABLE = "memory_record"

#: What "live" means for a context row, in one predicate. Both flags are filtered, so a row
#: retired through the memory layer is retired in this store too, and the lists take no flag of
#: their own: the opt-in door is the memory layer's own search.
_LIVE = "deleted_date IS NULL AND archived_date IS NULL"


def _table_for(scope: ContextScope) -> str:
    """The table one layer's rows live in."""
    return _AGENT_TABLE if scope is ContextScope.agent else _SHARED_TABLE

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
                "the agent layer is agent-scoped: pass agent=<domain>, or use "
                "list_shared_context for the cross-agent layer"
            )
        return agent

    # --- project context: discovery ------------------------------------------------

    def list_shared_context(self, *, project: str) -> Sequence[ContextEntry]:
        """The project's live shared entries, in theme order.

        Live means neither lifecycle flag is set, and the map's reserved title is excluded: a
        map row is not an entry and is read through ``get_map``.
        """
        return self._select_entries(project=project, scope=ContextScope.shared, agent=None)

    def list_agent_context(self, *, project: str, agent: str) -> Sequence[ContextEntry]:
        """The project's live entries for one agent, in theme order.

        ``agent`` is not optional, because a listing without one would answer with every
        agent's rows at once, which is the aggregate this layer exists to avoid.
        """
        return self._select_entries(
            project=project, scope=ContextScope.agent, agent=self._require_agent(agent)
        )

    # --- project context: creation -------------------------------------------------

    def create_shared_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Insert a shared entry, assigning its uuid.

        No theme check. A second row on one theme is an allowed state in this store, so the
        caller's own listing is what decides between creating and updating, and the store does
        not refuse the create or revive a retired row on its behalf.
        """
        return self._insert_entry(entry, agent=None)

    def create_agent_context_entry(self, entry: ContextEntry, *, agent: str) -> ContextEntry:
        """Insert one agent's entry, assigning its uuid.

        The owner is required, because `memory_record` carries `agent_id` and the token names
        the person rather than the agent, so no owner can be derived here.
        """
        return self._insert_entry(entry, agent=self._require_agent(agent))

    def _insert_entry(self, entry: ContextEntry, *, agent: str | None) -> ContextEntry:
        """One insert path for both layers: assign the uuid, write the row, read it back."""
        identity = str(_uuidlib.uuid4())
        created = entry.created_date or _now()
        modified = entry.modified_date or created
        tags = json.dumps(entry.tags or [])
        private = entry.scope is ContextScope.agent
        table = _table_for(entry.scope)
        # The two column lists differ by exactly the owner, so the value tuples do too. Written
        # out rather than spliced, because the version that removed `agent_id` by string surgery
        # was one rename away from silent drift.
        columns = _MEMORY_INSERT if private else _SHARED_INSERT
        values: tuple[object, ...] = (
            (identity, self._user_id, agent, RECORD_TYPE, entry.project, entry.theme, tags,
             created, modified, None, None, entry.full_content, entry.content_summary)
            if private
            else (identity, self._user_id, RECORD_TYPE, entry.project, entry.theme, tags,
                  created, modified, None, None, entry.full_content, entry.content_summary)
        )
        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})",
                    values,
                )
            except psycopg.errors.ForeignKeyViolation as exc:
                self._refuse_missing_agent(agent, exc)
            cur.execute(f"SELECT * FROM {table} WHERE uuid = %s", (identity,))
            row = cur.fetchone()
        return _row_to_entry(row, scope=entry.scope)

    def _refuse_missing_agent(self, agent: str | None, exc: Exception | None = None) -> None:
        raise MissingAgentError(
            f"no agent {agent!r} exists for this account, so it cannot own an agent-scoped "
            "context entry yet. Create the agent in the memory store first."
        ) from exc

    # --- project context: the direction change -------------------------------------

    def promote_context_entry(self, *, uuid: str) -> ContextEntry:
        """Move one entry from the agent layer into the shared one, keeping its uuid.

        Two steps in two transactions, because the layer is *which table* the row lives in:
        the target gains the row and the source is tombstoned, the same shape the markdown
        backend's copy-then-delete carries. A failure between them leaves the entry live in
        both layers and raises nothing, so a caller that needs to know reads both layers.
        """
        return self._move_entry(uuid=uuid, to_scope=ContextScope.shared)

    def demote_context_entry(self, *, uuid: str, agent: str) -> ContextEntry:
        """Move one entry from the shared layer into one agent's, keeping its uuid.

        The owner is required because the target row carries ``agent_id``: a shared row has no
        owner for it to inherit.
        """
        return self._move_entry(
            uuid=uuid, to_scope=ContextScope.agent, agent=self._require_agent(agent)
        )

    def _move_entry(
        self, *, uuid: str, to_scope: ContextScope, agent: str | None = None
    ) -> ContextEntry:
        source_scope = (
            ContextScope.agent if to_scope is ContextScope.shared else ContextScope.shared
        )
        source = _table_for(source_scope)
        target = _table_for(to_scope)
        with self._conn() as conn, conn.cursor() as cur:
            found = self._locate_entry(cur, uuid)
            if found is None:
                raise LookupError(f"no live context entry {uuid!r}")
            table, row = found
            if table != source:
                raise ValueError(
                    f"entry {uuid!r} already lives in the {to_scope.value} layer, so there is "
                    "nothing to move"
                )
            project = row["project"]
            theme = row["title"]
            # The target may not already hold a live row for that theme: moving onto another
            # entry would leave two rows on one theme by accident, which is the collision the
            # move sub-flow stops on.
            if self._theme_holder(
                cur, project=project, scope=to_scope, theme=theme, agent=agent
            ) is not None:
                raise ValueError(
                    f"cannot move {theme!r} to the {to_scope.value} layer: a live "
                    f"{to_scope.value} entry already holds that theme in {project!r}"
                )
            moved = _now()
            columns = _MEMORY_INSERT if to_scope is ContextScope.agent else _SHARED_INSERT
            values: tuple[object, ...] = (
                (uuid, self._user_id, agent, RECORD_TYPE, project, theme, row["tags"],
                 row["created_date"], moved, None, None, row["full_content"],
                 row["content_summary"])
                if to_scope is ContextScope.agent
                else (uuid, self._user_id, RECORD_TYPE, project, theme, row["tags"],
                      row["created_date"], moved, None, None, row["full_content"],
                      row["content_summary"])
            )
            try:
                cur.execute(
                    f"INSERT INTO {target} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})",
                    values,
                )
            except psycopg.errors.ForeignKeyViolation as exc:
                # The same enforcer the create leans on: moving into the agent layer names an
                # owner, and `memory_record` refuses one this account has no agent for. Mapped
                # here too, so a move reports it the way a create does.
                self._refuse_missing_agent(agent, exc)
            cur.execute(
                f"UPDATE {source} SET deleted_date = COALESCE(deleted_date, %s) "
                "WHERE uuid = %s AND user_id = %s",
                (moved, uuid, self._user_id),
            )
            cur.execute(f"SELECT * FROM {target} WHERE uuid = %s", (uuid,))
            row = cur.fetchone()
        return _row_to_entry(row, scope=to_scope)

    # --- the orientation map ------------------------------------------------------

    def get_map(self, *, project: str) -> OrientationMap | None:
        """The project's map, whole, or ``None``.

        One call answers both questions the flow asks, because an absent map comes back as
        ``None`` rather than as a separate existence check.
        """
        with self._conn() as conn, conn.cursor() as cur:
            row = self._select_map(cur, project=project)
        return _row_to_map(row) if row else None

    def create_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Insert the project's map, assigning its uuid.

        The title is reserved and one row deep, so the create looks for that row first and
        refuses a second map. Uniqueness in this store is the uuid's alone, so the refusal is a
        check rather than a constraint.
        """
        identity = str(_uuidlib.uuid4())
        created = orientation_map.created_date or _now()
        modified = orientation_map.modified_date or created
        with self._conn() as conn, conn.cursor() as cur:
            if self._select_map(cur, project=orientation_map.project) is not None:
                raise ValueError(
                    f"project {orientation_map.project!r} already has an orientation map; "
                    "write it instead of creating it"
                )
            cur.execute(
                f"INSERT INTO {_SHARED_TABLE} ({', '.join(_SHARED_INSERT)}) "
                f"VALUES ({', '.join(['%s'] * len(_SHARED_INSERT))})",
                (
                    identity, self._user_id, RECORD_TYPE, orientation_map.project, MAP_TITLE,
                    json.dumps([]), created, modified, None, None,
                    orientation_map.full_content, orientation_map.content_summary,
                ),
            )
            cur.execute(
                f"SELECT * FROM {_SHARED_TABLE} WHERE uuid = %s", (identity,)
            )
            row = cur.fetchone()
        return _row_to_map(row)

    def update_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Rewrite the project's map wholesale, found by its reserved title.

        Raises ``LookupError`` when the project has no live map to rewrite. Writing never
        creates one, which is what keeps a rewrite from quietly becoming a create.
        """
        with self._conn() as conn, conn.cursor() as cur:
            found = self._select_map(cur, project=orientation_map.project)
            if found is None:
                raise LookupError(
                    f"project {orientation_map.project!r} has no orientation map to write"
                )
            cur.execute(
                f"UPDATE {_SHARED_TABLE} SET full_content = %s, content_summary = %s, "
                "modified_date = %s WHERE uuid = %s AND user_id = %s",
                (
                    orientation_map.full_content,
                    orientation_map.content_summary,
                    _now(),
                    found["uuid"],
                    self._user_id,
                ),
            )
            cur.execute(
                f"SELECT * FROM {_SHARED_TABLE} WHERE uuid = %s", (found["uuid"],)
            )
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

    # --- the reads the ops above share ---------------------------------------------

    def _select_entries(
        self, *, project: str, scope: ContextScope, agent: str | None
    ) -> list[ContextEntry]:
        """A layer's live entries for one project, in theme order.

        Both lifecycle flags are filtered, so a row retired through the memory layer is retired
        here too. The map's reserved title is excluded, because a map row is not an entry.
        ``id`` breaks ties, since two rows may carry one theme.
        """
        where = [
            "user_id = %s",
            "record_type = %s",
            "project = %s",
            _LIVE,
            "title <> %s",
        ]
        params: list[object] = [self._user_id, RECORD_TYPE, project, MAP_TITLE]
        if agent is not None:
            where.append("agent_id = %s")
            params.append(agent)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {_table_for(scope)} WHERE {' AND '.join(where)} "
                "ORDER BY title, id",
                params,
            )
            rows = cur.fetchall()
        return [_row_to_entry(r, scope=scope) for r in rows]

    def _select_map(self, cur: psycopg.Cursor, *, project: str) -> dict | None:
        """The live map row for one project, or ``None``."""
        cur.execute(
            f"SELECT * FROM {_SHARED_TABLE} WHERE user_id = %s AND record_type = %s "
            f"AND project = %s AND title = %s AND {_LIVE}",
            (self._user_id, RECORD_TYPE, project, MAP_TITLE),
        )
        return cur.fetchone()

    def _theme_holder(
        self,
        cur: psycopg.Cursor,
        *,
        project: str,
        scope: ContextScope,
        theme: str,
        agent: str | None,
    ) -> dict | None:
        """The live row holding one theme in a layer, or ``None``. The move's collision check."""
        where = ["user_id = %s", "record_type = %s", "project = %s", "title = %s", _LIVE]
        params: list[object] = [self._user_id, RECORD_TYPE, project, theme]
        if agent is not None:
            where.append("agent_id = %s")
            params.append(agent)
        cur.execute(
            f"SELECT * FROM {_table_for(scope)} WHERE {' AND '.join(where)}", params
        )
        return cur.fetchone()

    def _locate_entry(self, cur: psycopg.Cursor, uuid: str) -> tuple[str, dict] | None:
        """The table and the live row holding one uuid, or ``None``.

        Both tables are probed, because ``uuid`` is unique *within* a table and not across the
        two. A uuid held by both is a state nothing should produce, so it raises rather than
        picking one: serving the wrong entry silently is the failure this read exists to avoid.
        The map's reserved title is excluded, since a map is not an entry.
        """
        found: list[tuple[str, dict]] = []
        for table in (_AGENT_TABLE, _SHARED_TABLE):
            cur.execute(
                f"SELECT * FROM {table} WHERE user_id = %s AND uuid = %s AND record_type = %s "
                f"AND {_LIVE} AND title <> %s",
                (self._user_id, uuid, RECORD_TYPE, MAP_TITLE),
            )
            row = cur.fetchone()
            if row is not None:
                found.append((table, row))
        if len(found) > 1:
            raise ValueError(
                f"uuid {uuid!r} is held by a live entry in both layers, so the store cannot "
                "tell which one was meant"
            )
        return found[0] if found else None


def _row_to_entry(row: dict, *, scope: ContextScope) -> ContextEntry:
    """One entry, with the layer its caller already knows: the table it was read from."""
    return ContextEntry(
        uuid=row["uuid"],
        id=row["id"],
        project=row["project"],
        scope=scope,
        theme=row["title"],
        content_summary=row["content_summary"],
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
        content_summary=row["content_summary"],
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
