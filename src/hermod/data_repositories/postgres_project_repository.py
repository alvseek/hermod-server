"""PostgreSQL implementation of ``ProjectRepository`` (Hermod's project store).

The deployed twin of ``SqliteProjectRepository``: the same operations, the same tombstones and
the same partial unique index, against the database the deployment uses. Munnin splits its own
store the same way and for the same reason — Postgres in the deployment, SQLite for tests and
local use — so the two engines have to agree on behaviour, and one contract test proves they do.

**It is a second database on the same instance, not a second schema inside Munnin's.** That is
the record: the `munnin-deploy` Postgres plan's decision 11 — *"a second database on the same
instance, later: one engine and one box"*. It also keeps the two stores' migration runners from
writing one ``migration_history`` table, which a shared database would force them to resolve.

Design mirrors ``SqliteProjectRepository``: one connection per operation, schema applied lazily
on the first operation, and no connection pool. A pool is a later optimization rather than a
correctness need at this load, which is the same call Munnin's Postgres repository made.

Two differences are Postgres's rather than choices:

- placeholders are ``%s``, and a statement needs a cursor;
- a refused duplicate is ``psycopg.errors.UniqueViolation``, a *class* rather than a message, so
  the narrowing the SQLite backend has to do by string match is exact here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from hermod.data_entities.postgres_migrations import ensure_schema
from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap

# One source of truth for column order, shared with the SQLite backend's shape.
_ENTRY_COL = (
    "id", "project", "scope", "theme", "description", "tags",
    "created_date", "modified_date", "deleted_date", "full_content",
)
_ENTRY_COLUMNS = ", ".join(_ENTRY_COL)
_MAP_COL = ("id", "project", "description", "created_date", "modified_date", "full_content")
_MAP_COLUMNS = ", ".join(_MAP_COL)

#: The identity a create must not already hold, and the scope a move must not collide with.
_ENTRY_IDENTITY = "project=%s AND scope=%s AND theme=%s AND deleted_date IS NULL"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PostgresProjectRepository:
    """Implements ``ProjectRepository`` (structural)."""

    def __init__(self, db_url: str) -> None:
        self._dsn = db_url
        self._ensured = False

    # --- connection / schema ---

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._dsn, row_factory=dict_row)
        try:
            if not self._ensured:
                ensure_schema(conn)
                self._ensured = True
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- project context: reads ---

    def list_context_entries(
        self, *, project: str, scope: ContextScope | None = None
    ) -> Sequence[ContextEntry]:
        where = ["project = %s", "deleted_date IS NULL"]
        params: list[object] = [project]
        if scope is not None:
            where.append("scope = %s")
            params.append(scope.value)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context "
                f"WHERE {' AND '.join(where)} ORDER BY scope, theme",
                params,
            )
            rows = cur.fetchall()
        return [_row_to_entry(r) for r in rows]

    def get_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> ContextEntry | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context WHERE {_ENTRY_IDENTITY}",
                (project, scope.value, theme),
            )
            row = cur.fetchone()
        return _row_to_entry(row) if row else None

    # --- project context: writes ---

    def create_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Insert one entry, refusing to touch an existing live one.

        The refusal is the schema's partial unique index, translated into ``ValueError`` so a
        caller sees "that theme already exists" rather than an integrity string. The exception
        class is the narrowing: ``UniqueViolation`` is this statement's only possible
        constraint failure, so nothing else is misreported as a collision.
        """
        created = entry.created_date or _now()
        modified = entry.modified_date or created
        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO project_context "
                    "(project, scope, theme, description, tags, created_date, "
                    " modified_date, deleted_date, full_content) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        entry.project,
                        entry.scope.value,
                        entry.theme,
                        entry.description,
                        json.dumps(entry.tags or []),
                        created,
                        modified,
                        entry.deleted_date,
                        entry.full_content,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(
                    f"a {entry.scope.value} context entry for {entry.project!r} already "
                    f"exists under theme {entry.theme!r}; update it instead of creating it"
                ) from exc
            cur.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context WHERE {_ENTRY_IDENTITY}",
                (entry.project, entry.scope.value, entry.theme),
            )
            row = cur.fetchone()
        return _row_to_entry(row)

    def update_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Replace the live entry's body, description and tags. ``created_date`` is preserved;
        ``modified_date`` is bumped."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE project_context SET full_content=%s, description=%s, tags=%s, "
                "modified_date=%s WHERE " + _ENTRY_IDENTITY,
                (
                    entry.full_content,
                    entry.description,
                    json.dumps(entry.tags or []),
                    _now(),
                    entry.project,
                    entry.scope.value,
                    entry.theme,
                ),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"no live {entry.scope.value} context entry {entry.theme!r} "
                    f"in project {entry.project!r}"
                )
            cur.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context WHERE {_ENTRY_IDENTITY}",
                (entry.project, entry.scope.value, entry.theme),
            )
            row = cur.fetchone()
        return _row_to_entry(row)

    def move_context_entry(
        self,
        *,
        project: str,
        theme: str,
        from_scope: ContextScope,
        to_scope: ContextScope,
    ) -> ContextEntry:
        """Change the entry's scope, body untouched. A single UPDATE where the markdown backend
        copies then deletes; the body is never read, so it cannot drift."""
        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    "UPDATE project_context SET scope=%s, modified_date=%s "
                    "WHERE project=%s AND theme=%s AND scope=%s AND deleted_date IS NULL",
                    (to_scope.value, _now(), project, theme, from_scope.value),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(
                    f"cannot move {theme!r} to {to_scope.value}: a live "
                    f"{to_scope.value} entry already holds that theme in {project!r}"
                ) from exc
            if cur.rowcount == 0:
                raise LookupError(
                    f"no live {from_scope.value} context entry {theme!r} in project {project!r}"
                )
            cur.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context WHERE {_ENTRY_IDENTITY}",
                (project, to_scope.value, theme),
            )
            row = cur.fetchone()
        return _row_to_entry(row)

    def delete_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> None:
        """Tombstone the entry. Idempotent — an already-tombstoned entry keeps its first
        timestamp (``COALESCE``), so a repeated delete is not an error. A theme that was never
        present at all is a ``LookupError``."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE project_context SET deleted_date=COALESCE(deleted_date, %s) "
                "WHERE project=%s AND scope=%s AND theme=%s",
                (_now(), project, scope.value, theme),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"no {scope.value} context entry {theme!r} in project {project!r}"
                )

    # --- the orientation map ---

    def map_exists(self, *, project: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM orientation_map WHERE project=%s", (project,))
            found = cur.fetchone()
        return found is not None

    def get_map(self, *, project: str) -> OrientationMap | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_MAP_COLUMNS} FROM orientation_map WHERE project=%s", (project,)
            )
            row = cur.fetchone()
        return _row_to_map(row) if row else None

    def create_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Insert the project's map. A project has at most one, so a second create is a
        ``ValueError`` rather than a silent overwrite; editing goes through ``write_map``."""
        created = orientation_map.created_date or _now()
        modified = orientation_map.modified_date or created
        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO orientation_map "
                    "(project, description, created_date, modified_date, full_content) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        orientation_map.project,
                        orientation_map.description,
                        created,
                        modified,
                        orientation_map.full_content,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(
                    f"project {orientation_map.project!r} already has an orientation map; "
                    "write it instead of creating it"
                ) from exc
            cur.execute(
                f"SELECT {_MAP_COLUMNS} FROM orientation_map WHERE project=%s",
                (orientation_map.project,),
            )
            row = cur.fetchone()
        return _row_to_map(row)

    def write_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Rewrite the map wholesale, bumping ``modified_date``. Raises ``LookupError`` when
        there is no map to write, so a caller cannot create one by accident."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE orientation_map SET full_content=%s, description=%s, modified_date=%s "
                "WHERE project=%s",
                (
                    orientation_map.full_content,
                    orientation_map.description,
                    _now(),
                    orientation_map.project,
                ),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"project {orientation_map.project!r} has no orientation map to write"
                )
            cur.execute(
                f"SELECT {_MAP_COLUMNS} FROM orientation_map WHERE project=%s",
                (orientation_map.project,),
            )
            row = cur.fetchone()
        return _row_to_map(row)


def _row_to_entry(row: dict) -> ContextEntry:
    return ContextEntry(
        id=row["id"],
        project=row["project"],
        scope=ContextScope(row["scope"]),
        theme=row["theme"],
        description=row["description"],
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
        description=row["description"],
        created_date=row["created_date"],
        modified_date=row["modified_date"],
        full_content=row["full_content"],
    )
