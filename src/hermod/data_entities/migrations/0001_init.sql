-- Hermod's project store — the project-keyed memory the overlay's `db` backend defers to
-- (ADR-023). Two tables, one per entity.
--
--   project_context   a project's context entries: shared or private, one live row per theme.
--   orientation_map   one map per project, stored whole.
--
-- `project_context`'s identity is (project, scope, theme) — the path the markdown backend
-- writes minus its directory — so the same entry keeps its identity when it moves between
-- scopes. The unique index is partial on `deleted_date IS NULL`: a tombstone is not a live
-- entry, so deleting a theme and creating it again is a new file, exactly as on disk.
--
-- No foreign keys: nothing here references another table. No FTS index: the store is
-- per-project and small, and every read is a filtered list, so the browse index below is
-- the whole access pattern.

CREATE TABLE IF NOT EXISTS project_context (
  id            INTEGER PRIMARY KEY,
  project       TEXT NOT NULL,
  scope         TEXT NOT NULL CHECK (scope IN ('shared', 'private')),
  theme         TEXT NOT NULL,
  description   TEXT,
  tags          TEXT,                   -- JSON array (stored as text)
  created_date  TEXT NOT NULL,
  modified_date TEXT NOT NULL,
  deleted_date  TEXT,                   -- non-NULL = tombstone, excluded from all reads
  full_content  TEXT                    -- the whole entry document; last column
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_context_identity
  ON project_context (project, scope, theme)
  WHERE deleted_date IS NULL;

CREATE INDEX IF NOT EXISTS idx_project_context_browse
  ON project_context (project, scope, created_date);

CREATE TABLE IF NOT EXISTS orientation_map (
  id            INTEGER PRIMARY KEY,
  project       TEXT NOT NULL UNIQUE,
  description   TEXT,
  created_date  TEXT NOT NULL,
  modified_date TEXT NOT NULL,
  full_content  TEXT
);
