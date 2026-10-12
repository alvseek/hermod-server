"""The store seam (DI boundary) for Hermod's project memory.

The server depends on this Protocol, never on a concrete database. v1 ships
``SqliteProjectRepository``; a second backend swaps in by DI with no caller change, which is
the same shape Munnin's ``MemoryRepository`` uses.

Two entity families, each addressed the way the markdown backend addresses a file:

- **context entries** by ``(project, scope, theme)`` — create, read, update, move between
  scopes, and tombstone.
- **the orientation map** by ``project`` — exists, read, create and rewrite.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap


class ProjectRepository(Protocol):
    """Read/write API over Hermod's project store."""

    # --- project context ---

    def list_context_entries(
        self, *, project: str, scope: ContextScope | None = None
    ) -> Sequence[ContextEntry]:
        """A project's live entries, optionally narrowed to one scope. Tombstones are
        never returned, which is what makes ``delete`` a removal to every reader."""
        ...

    def get_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> ContextEntry | None:
        """One live entry by its identity, or ``None`` if absent or tombstoned."""
        ...

    def create_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Insert one entry. Raises ``ValueError`` if a live entry already holds this
        ``(project, scope, theme)`` — creating over a theme is a caller mistake, not an
        update, so it is refused rather than absorbed."""
        ...

    def update_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Replace a live entry's body, description and tags. Bumps ``modified_date`` and
        preserves ``created_date``. Raises ``LookupError`` if no live entry holds the
        identity."""
        ...

    def move_context_entry(
        self,
        *,
        project: str,
        theme: str,
        from_scope: ContextScope,
        to_scope: ContextScope,
    ) -> ContextEntry:
        """Change an entry's scope, body untouched — the DB's one-step form of the
        markdown backend's copy-then-delete. Raises ``LookupError`` if no live entry holds
        the source identity, ``ValueError`` if the target already holds one (the collision
        the move sub-flow exists to catch)."""
        ...

    def delete_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> None:
        """Tombstone one entry (idempotent — the first timestamp is preserved). Raises
        ``LookupError`` if the identity was never present at all."""
        ...

    # --- the orientation map ---

    def map_exists(self, *, project: str) -> bool:
        """Whether the project has a map. The served ``§ map-exists``."""
        ...

    def get_map(self, *, project: str) -> OrientationMap | None:
        """The project's map, whole, or ``None``."""
        ...

    def create_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Insert the project's map. Raises ``ValueError`` if it already has one — a map
        is created once and edited thereafter."""
        ...

    def write_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Rewrite the project's map wholesale. Bumps ``modified_date``; raises
        ``LookupError`` if there is no map to write."""
        ...
