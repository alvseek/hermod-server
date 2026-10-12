"""SQLite implementation of ``ProjectRepository`` (Hermod's project store).

Design mirrors Munnin's ``SqliteMemoryRepository``: one connection per operation (SQLite
open is microseconds, WAL persists across connections), schema applied lazily on the first
operation, and ``:memory:`` therefore does not persist between operations — tests use a
temp file.

Two choices worth naming:

- **Every read is tenancy-free but project-scoped.** There is no tenant column: this store
  belongs to one machine and one user, and ``project`` is the scope every op carries.
- **``deleted_date`` is a tombstone, never a DELETE.** A reader never sees it, and the
  partial unique index lets the same theme be created again as a new entry afterwards,
  which is exactly what removing a file and recreating it does on the markdown backend.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_entities.schema_migrations import apply_migrations

# One source of truth for column order.
_ENTRY_COL = (
    "id", "project", "scope", "theme", "description", "tags",
    "created_date", "modified_date", "deleted_date", "full_content",
)
_ENTRY_COLUMNS = ", ".join(_ENTRY_COL)
# Insert set = every column except the autoincrement id.
_ENTRY_INSERT_COLUMNS = ", ".join(_ENTRY_COL[1:])
_MAP_COL = ("id", "project", "description", "created_date", "modified_date", "full_content")
_MAP_COLUMNS = ", ".join(_MAP_COL)
_MAP_INSERT_COLUMNS = ", ".join(_MAP_COL[1:])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteProjectRepository:
    """Implements ``ProjectRepository`` (structural)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._ensured = False

    # --- connection / schema ---

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        if not self._ensured:
            apply_migrations(conn)
            self._ensured = True
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- project context: reads ---

    def list_context_entries(
        self, *, project: str, scope: ContextScope | None = None
    ) -> Sequence[ContextEntry]:
        where = ["project = ?", "deleted_date IS NULL"]
        params: list[object] = [project]
        if scope is not None:
            where.append("scope = ?")
            params.append(scope.value)
        sql = (
            f"SELECT {_ENTRY_COLUMNS} FROM project_context "
            f"WHERE {' AND '.join(where)} ORDER BY scope, theme"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> ContextEntry | None:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context "
                "WHERE project=? AND scope=? AND theme=? AND deleted_date IS NULL",
                (project, scope.value, theme),
            ).fetchone()
        return _row_to_entry(row) if row else None

    # --- project context: writes ---

    def create_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Insert one entry, refusing to touch an existing live one.

        The refusal is the schema's partial unique index; it is translated into
        ``ValueError`` so a caller sees "that theme already exists" rather than an opaque
        integrity string."""
        created = entry.created_date or _now()
        modified = entry.modified_date or created
        with self._conn() as conn:
            try:
                conn.execute(
                    f"""
                    INSERT INTO project_context ({_ENTRY_INSERT_COLUMNS})
                    VALUES (:project, :scope, :theme, :description, :tags,
                            :created_date, :modified_date, :deleted_date, :full_content)
                    """,
                    {
                        "project": entry.project,
                        "scope": entry.scope.value,
                        "theme": entry.theme,
                        "description": entry.description,
                        "tags": json.dumps(entry.tags or []),
                        "created_date": created,
                        "modified_date": modified,
                        "deleted_date": entry.deleted_date,
                        "full_content": entry.full_content,
                    },
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" not in str(exc):
                    raise
                raise ValueError(
                    f"a {entry.scope.value} context entry for {entry.project!r} already "
                    f"exists under theme {entry.theme!r}; update it instead of creating it"
                ) from exc
            row = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context "
                "WHERE project=? AND scope=? AND theme=? AND deleted_date IS NULL",
                (entry.project, entry.scope.value, entry.theme),
            ).fetchone()
        return _row_to_entry(row)

    def update_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Replace the live entry's body, description and tags. ``created_date`` is
        preserved; ``modified_date`` is bumped."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE project_context SET full_content=?, description=?, tags=?, "
                "modified_date=? "
                "WHERE project=? AND scope=? AND theme=? AND deleted_date IS NULL",
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
            if cursor.rowcount == 0:
                raise LookupError(
                    f"no live {entry.scope.value} context entry {entry.theme!r} "
                    f"in project {entry.project!r}"
                )
            row = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context "
                "WHERE project=? AND scope=? AND theme=? AND deleted_date IS NULL",
                (entry.project, entry.scope.value, entry.theme),
            ).fetchone()
        return _row_to_entry(row)

    def move_context_entry(
        self,
        *,
        project: str,
        theme: str,
        from_scope: ContextScope,
        to_scope: ContextScope,
    ) -> ContextEntry:
        """Change the entry's scope, body untouched. A single UPDATE where the markdown
        backend copies then deletes; the body is never read, so it cannot drift."""
        with self._conn() as conn:
            try:
                cursor = conn.execute(
                    "UPDATE project_context SET scope=?, modified_date=? "
                    "WHERE project=? AND theme=? AND scope=? AND deleted_date IS NULL",
                    (to_scope.value, _now(), project, theme, from_scope.value),
                )
            except sqlite3.IntegrityError as exc:
                # Narrowed, as ``create_context_entry`` and ``create_map`` are: the partial unique
                # index is the only constraint this statement can violate today, and a blanket
                # catch would report some future one as a collision.
                if "UNIQUE constraint failed" not in str(exc):
                    raise
                raise ValueError(
                    f"cannot move {theme!r} to {to_scope.value}: a live "
                    f"{to_scope.value} entry already holds that theme in {project!r}"
                ) from exc
            if cursor.rowcount == 0:
                raise LookupError(
                    f"no live {from_scope.value} context entry {theme!r} in project {project!r}"
                )
            row = conn.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM project_context "
                "WHERE project=? AND scope=? AND theme=? AND deleted_date IS NULL",
                (project, to_scope.value, theme),
            ).fetchone()
        return _row_to_entry(row)

    def delete_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> None:
        """Tombstone the entry. Idempotent — an already-tombstoned entry keeps its first
        timestamp (``COALESCE``), so a repeated delete is not an error. A theme that was
        never present at all is a ``LookupError``."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE project_context SET deleted_date=COALESCE(deleted_date, ?) "
                "WHERE project=? AND scope=? AND theme=?",
                (_now(), project, scope.value, theme),
            )
            if cursor.rowcount == 0:
                raise LookupError(
                    f"no {scope.value} context entry {theme!r} in project {project!r}"
                )

    # --- the orientation map ---

    def map_exists(self, *, project: str) -> bool:
        with self._conn() as conn:
            found = conn.execute(
                "SELECT 1 FROM orientation_map WHERE project=?", (project,)
            ).fetchone()
        return found is not None

    def get_map(self, *, project: str) -> OrientationMap | None:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_MAP_COLUMNS} FROM orientation_map WHERE project=?", (project,)
            ).fetchone()
        return _row_to_map(row) if row else None

    def create_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Insert the project's map. A project has at most one, so a second create is a
        ``ValueError`` rather than a silent overwrite — editing goes through ``write_map``."""
        created = orientation_map.created_date or _now()
        modified = orientation_map.modified_date or created
        with self._conn() as conn:
            try:
                conn.execute(
                    f"""
                    INSERT INTO orientation_map ({_MAP_INSERT_COLUMNS})
                    VALUES (:project, :description, :created_date, :modified_date,
                            :full_content)
                    """,
                    {
                        "project": orientation_map.project,
                        "description": orientation_map.description,
                        "created_date": created,
                        "modified_date": modified,
                        "full_content": orientation_map.full_content,
                    },
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" not in str(exc):
                    raise
                raise ValueError(
                    f"project {orientation_map.project!r} already has an orientation map; "
                    "write it instead of creating it"
                ) from exc
            row = conn.execute(
                f"SELECT {_MAP_COLUMNS} FROM orientation_map WHERE project=?",
                (orientation_map.project,),
            ).fetchone()
        return _row_to_map(row)

    def write_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Rewrite the map wholesale, bumping ``modified_date``. Raises ``LookupError``
        when there is no map to write, so a caller cannot create one by accident."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE orientation_map SET full_content=?, description=?, modified_date=? "
                "WHERE project=?",
                (
                    orientation_map.full_content,
                    orientation_map.description,
                    _now(),
                    orientation_map.project,
                ),
            )
            if cursor.rowcount == 0:
                raise LookupError(
                    f"project {orientation_map.project!r} has no orientation map to write"
                )
            row = conn.execute(
                f"SELECT {_MAP_COLUMNS} FROM orientation_map WHERE project=?",
                (orientation_map.project,),
            ).fetchone()
        return _row_to_map(row)


def _row_to_entry(row: sqlite3.Row) -> ContextEntry:
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


def _row_to_map(row: sqlite3.Row) -> OrientationMap:
    return OrientationMap(
        id=row["id"],
        project=row["project"],
        description=row["description"],
        created_date=row["created_date"],
        modified_date=row["modified_date"],
        full_content=row["full_content"],
    )
