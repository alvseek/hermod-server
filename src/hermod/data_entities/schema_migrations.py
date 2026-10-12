"""Schema migrations for Hermod's project store.

The schema is the ordered SQL files under ``migrations/``, applied through a
``migration_history`` table, so a change actually reaches a database that already exists.
That is the one thing ``CREATE TABLE IF NOT EXISTS`` cannot do, and the whole reason this
module exists — carried from Munnin's runner, which learned it the same way.

No baseline step is needed: ``0001_init`` is written with ``IF NOT EXISTS``, so it is a
no-op against a store that already carries the base schema, and every later migration then
runs exactly once on both a fresh and an existing database.

Each migration runs inside its own transaction and is recorded only on success. An
already-applied migration is checked against its stored checksum, so editing a file that
has already run is refused rather than silently doing nothing.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS migration_history (
  version    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  applied_at TEXT NOT NULL
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


def _applied(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        version: checksum
        for version, checksum in conn.execute("SELECT version, checksum FROM migration_history")
    }


def status(conn: sqlite3.Connection) -> list[tuple[Migration, str]]:
    """``(migration, state)`` for every file, in order. State is applied | pending | changed."""
    conn.executescript(_HISTORY_DDL)
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


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply every pending migration in order; return the names applied.

    Idempotent: a second call with nothing pending returns ``[]``. Raises if a migration
    that already ran has since been edited.
    """
    conn.executescript(_HISTORY_DDL)
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
        # One transaction per migration, so a failure leaves nothing half-applied.
        conn.executescript(f"BEGIN;\n{m.sql}\nCOMMIT;")
        conn.execute(
            "INSERT INTO migration_history (version, name, checksum, applied_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (m.version, m.name, m.checksum),
        )
        conn.commit()
        ran.append(m.name)
    return ran
