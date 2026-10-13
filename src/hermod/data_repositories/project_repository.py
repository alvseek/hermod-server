"""The store seam (DI boundary) for Hermod's project memory.

The server depends on this Protocol, never on a concrete database. ``RecordProjectRepository`` is
what implements it, over Valaskjalf's record tables, which is the same shape Munnin's
``MemoryRepository`` uses.

Two entity families, each addressed the way the markdown backend addresses a file:

- **context entries** by ``(project, scope, theme)`` — create, read, update, move between
  scopes, and tombstone.
- **the orientation map** by ``project`` — exists, read, create and rewrite.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from hermod.data_entities.awakening import CodingPattern, Episode, EpisodeIndexEntry
from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap


class ProjectRepository(Protocol):
    """Read/write API over Hermod's project store.

    Every op that can touch the **private** scope takes ``agent``, because that scope is
    agent-scoped: `memory_record` carries ``agent_id``, and a private read without one would
    answer with every agent's rows at once. The token names the person, never the agent, so it
    cannot be derived here — it is the caller's to pass, and the store refuses the ops that need
    it and do not get it.
    """

    # --- project context ---

    def list_context_entries(
        self,
        *,
        project: str,
        scope: ContextScope | None = None,
        agent: str | None = None,
    ) -> Sequence[ContextEntry]:
        """A project's live entries, optionally narrowed to one scope. Tombstones are
        never returned, which is what makes ``delete`` a removal to every reader.
        ``agent`` is required once the private scope is in play — which it is when
        ``scope`` is omitted — and refused without it rather than answered."""
        ...

    def get_context_entry(
        self,
        *,
        project: str,
        scope: ContextScope,
        theme: str,
        agent: str | None = None,
    ) -> ContextEntry | None:
        """One live entry by its identity, or ``None`` if absent or tombstoned.
        ``agent`` is required for the private scope."""
        ...

    def create_context_entry(
        self, entry: ContextEntry, *, agent: str | None = None
    ) -> ContextEntry:
        """Insert one entry. Raises ``ValueError`` if a live entry already holds this
        ``(project, scope, theme)`` — creating over a theme is a caller mistake, not an
        update, so it is refused rather than absorbed. ``agent`` is required for the
        private scope, where it is part of the identity rather than a label: the same
        theme under two agents is two entries."""
        ...

    def update_context_entry(
        self, entry: ContextEntry, *, agent: str | None = None
    ) -> ContextEntry:
        """Replace a live entry's body, description and tags. Bumps ``modified_date`` and
        preserves ``created_date``. Raises ``LookupError`` if no live entry holds the
        identity. ``agent`` is required for the private scope."""
        ...

    def move_context_entry(
        self,
        *,
        project: str,
        theme: str,
        from_scope: ContextScope,
        to_scope: ContextScope,
        agent: str | None = None,
    ) -> ContextEntry:
        """Change an entry's scope, body untouched. Two steps underneath, because the
        scope is *which table* the row lives in: the target gains the row and the source's
        is tombstoned, the same shape as the markdown backend's copy-then-delete. Raises
        ``LookupError`` if no live entry holds the source identity, ``ValueError`` if the
        target already holds one (the collision the move sub-flow exists to catch).
        ``agent`` is required for whichever side is private."""
        ...

    def delete_context_entry(
        self, *, project: str, scope: ContextScope, theme: str, agent: str | None = None
    ) -> None:
        """Tombstone one entry (idempotent — the first timestamp is preserved). Raises
        ``LookupError`` if the identity was never present at all. ``agent`` is required
        for the private scope."""
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

    # --- the memory rows the coding awakening reads ---
    #
    # Munnin owns both of these. Hermod reads them for exactly one purpose, assembling the
    # coding half of an awakening, and never writes, retires or repairs them (ADR-029).

    def newest_episode_per_project(
        self, *, agent: str, projects: Sequence[str]
    ) -> Sequence[Episode]:
        """The newest live episode for each of ``projects``, in the set's own order.

        One per project, and a project with no live episode is simply absent from the
        result. Archived and tombstoned rows are excluded, the same thing Munnin's own reads
        do, because a retired episode is not context to carry forward. Episodes are
        agent-scoped, so ``agent`` is required rather than optional."""
        ...

    def coding_lane(self) -> Sequence[CodingPattern]:
        """The whole coding-reasoning lane, in title order.

        Shared memory with no project and no owner, which is what keeps coding patterns out
        of every other agent's awakening. No agent and no scope apply, so neither is taken
        here."""
        ...

    def episode_index(
        self, *, agent: str, projects: Sequence[str]
    ) -> Sequence[EpisodeIndexEntry]:
        """Every live episode of the set, newest first, **without bodies**.

        The index half of the awakening's episodic part: the newest body per project is what
        carries work forward, and the rest of the set is what tells a caller what else is
        there. Named columns rather than ``*``, because the point of this read is that it does
        not drag bodies a caller will not use. Episodes are agent-scoped, so ``agent`` is
        required."""
        ...
