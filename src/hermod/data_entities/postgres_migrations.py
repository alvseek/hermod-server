"""Schema migrations for the PostgreSQL store.

The Postgres twin of ``schema_migrations.py``: the same discipline — ordered SQL files applied
through a ``migration_history`` table, each recorded with a checksum so an applied file can never
be silently edited — written separately rather than shared, because the two runners govern two
engines and the SQLite runner carries the local store's applied history. Munnin's pair is split
the same way for the same reason.

Two things differ from the SQLite runner, both because Postgres can do what SQLite cannot:

- **No table-rebuild dance.** Postgres can ``ALTER`` a constraint in place, so the migration is
  authored at the final shape and there is no equivalent of a rebuild migration.
- **Whole-file apply.** psycopg runs a parameterless multi-statement string through the simple
  query protocol, so a migration file executes in one call and one transaction; there is no
  ``executescript``.

The schema is applied on the repository's first connection, the same contract
``SqliteProjectRepository`` keeps.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

#: Postgres migrations live apart from the SQLite ones; the SQLite runner globs
#: ``migrations/*.sql`` non-recursively, so this sibling directory is invisible to it.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations_postgres"

_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS migration_history (
  version    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    """One migration file. ``version`` is the leading number, ``name`` the file stem."""

    version: str
    name: str
    sql: str
    checksum: str


def known() -> list[Migration]:
    """Every migration file, ordered by name."""
    found: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        found.append(
            Migration(
                version=path.stem.split("_", 1)[0],
                name=path.stem,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return found


def _applied(conn: psycopg.Connection) -> dict[str, str]:
    # Force tuple rows: the caller's connection uses a dict row factory, and this reader
    # unpacks by position.
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT version, checksum FROM migration_history")
        return {version: checksum for version, checksum in cur.fetchall()}


def status(conn: psycopg.Connection) -> list[tuple[Migration, str]]:
    """``(migration, state)`` for every file, in order. State is applied | pending | changed."""
    with conn.cursor() as cur:
        cur.execute(_HISTORY_DDL)
    conn.commit()
    applied = _applied(conn)
    out: list[tuple[Migration, str]] = []
    for m in known():
        if m.version not in applied:
            out.append((m, "pending"))
        elif applied[m.version] != m.checksum:
            out.append((m, "changed"))
        else:
            out.append((m, "applied"))
    return out


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    """Apply every pending migration in order; return the names applied.

    Idempotent: a second call with nothing pending returns ``[]``. Raises if a migration that
    already ran has since been edited.
    """
    with conn.cursor() as cur:
        cur.execute(_HISTORY_DDL)
    conn.commit()
    applied = _applied(conn)
    ran: list[str] = []
    for m in known():
        if m.version in applied:
            if applied[m.version] != m.checksum:
                raise RuntimeError(
                    f"migration {m.name!r} changed after it was applied "
                    f"(recorded {applied[m.version][:12]}, now {m.checksum[:12]})"
                )
            continue
        # One transaction per migration: the file's statements and the history row commit
        # together, so a failure leaves nothing half-applied.
        with conn.cursor() as cur:
            cur.execute(m.sql)
            cur.execute(
                "INSERT INTO migration_history (version, name, checksum, applied_at) "
                "VALUES (%s, %s, %s, now())",
                (m.version, m.name, m.checksum),
            )
        conn.commit()
        ran.append(m.name)
    return ran


def ensure_schema(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations on a connection (the repository's first-connection call).

    Returns the names applied; ``[]`` when the schema is already current.
    """
    return apply_migrations(conn)
