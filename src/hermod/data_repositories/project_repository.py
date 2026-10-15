"""The store seam (DI boundary) for Hermod's project memory.

The server depends on this Protocol, never on a concrete database. ``RecordProjectRepository`` is
what implements it, over Valaskjalf's record tables, which is the same shape Munnin's
``MemoryRepository`` uses.

Two entity families:

- **context entries** by their ``uuid``, which the store assigns at insert — discover them per
  layer, create them, and move one between layers.
- **the orientation map** by ``project`` — read, create and update.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from hermod.data_entities.awakening import CodingPattern, Episode, EpisodeIndexEntry
from hermod.data_entities.project_context import ContextEntry, OrientationMap


class ProjectRepository(Protocol):
    """Discovery and creation over Hermod's project store.

    Everything addressed by a uuid that already exists is the memory layer's own: its ``get``,
    its ``edit`` family, ``edit_metadata`` and ``soft_delete`` already act on these rows, so
    this store does not implement a second version of any of them.

    Every op that can touch the **agent** layer takes ``agent``, because that layer is
    agent-scoped: `memory_record` carries ``agent_id``, and a listing without one would answer
    with every agent's rows at once. The token names the person, never the agent, so it cannot
    be derived here — it is the caller's to pass, and the store refuses the ops that need it and
    do not get it.
    """

    # --- project context: discovery ---

    def list_shared_context(self, *, project: str) -> Sequence[ContextEntry]:
        """The project's live shared entries, in theme order. A row with either lifecycle flag
        set is retired and never returned, and the map's reserved title is excluded: a map row
        is not an entry."""
        ...

    def list_agent_context(self, *, project: str, agent: str) -> Sequence[ContextEntry]:
        """One agent's live entries for a project, in theme order. ``agent`` is required rather
        than optional, because omitting it could only answer with every agent's rows at once."""
        ...

    # --- project context: creation ---

    def create_shared_context_entry(self, entry: ContextEntry) -> ContextEntry:
        """Insert a shared entry, assigning its uuid, which the result carries. No theme is
        checked: a second row on one theme is an allowed state, so the caller's own listing is
        what decides between creating and updating."""
        ...

    def create_agent_context_entry(self, entry: ContextEntry, *, agent: str) -> ContextEntry:
        """Insert one agent's entry, assigning its uuid. ``agent`` must be an existing agent
        for this account, because `memory_record` carries the owner and the token cannot supply
        one. Raises ``MissingAgentError`` when it does not exist."""
        ...

    # --- project context: the direction change ---

    def promote_context_entry(self, *, uuid: str) -> ContextEntry:
        """Move one entry from the agent layer into the shared one, keeping its uuid.

        Two steps underneath, because the layer is *which table* the row lives in: the target
        gains the row and the source is tombstoned, the same shape the markdown backend's
        copy-then-delete carries. Raises ``LookupError`` when no live entry holds the uuid, and
        ``ValueError`` when it is already in the target layer or when the target already holds
        that theme."""
        ...

    def demote_context_entry(self, *, uuid: str, agent: str) -> ContextEntry:
        """Move one entry from the shared layer into one agent's, keeping its uuid. The owner is
        required, because the target row carries ``agent_id`` and a shared row has none to
        inherit."""
        ...

    # --- the orientation map ---

    def get_map(self, *, project: str) -> OrientationMap | None:
        """The project's map, whole, or ``None``. The served ``§ read-map``, which answers the
        existence question too: an absent map is ``None``."""
        ...

    def create_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Insert the project's map, assigning its uuid. Raises ``ValueError`` if the project
        already has one — a map is created once and updated thereafter."""
        ...

    def update_map(self, orientation_map: OrientationMap) -> OrientationMap:
        """Rewrite the project's map wholesale. Bumps ``modified_date``; raises ``LookupError``
        if there is no map to write."""
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
