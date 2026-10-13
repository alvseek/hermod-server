"""The coding awakening's payload, assembled in one call.

The one service here that returns a *payload* rather than an entity: it exists because a coding
session needs several things before it starts, and asking for them one at a time is what made an
awakening four calls (ADR-029). It composes reads and adds no rule of its own.

Two of its parts are Munnin's records — the episodes and the coding lane — and two are this
server's — the project context and the orientation map. Nothing here writes anything, which is
what keeps the reach narrow: this service filters and returns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from hermod.data_entities.awakening import CodingPattern, Episode, EpisodeIndexEntry
from hermod.data_entities.project_context import ContextEntry, OrientationMap
from hermod.data_repositories.project_repository import ProjectRepository


class AwakeningService:
    """The coding half of an awakening, in one read-only call."""

    def __init__(self, repository_for: Callable[[], ProjectRepository]) -> None:
        # A factory for the same reason ContextService takes one: the tenant is a property of the
        # verified token on *this* call, never of the process.
        self._repository_for = repository_for

    def awaken(self, *, domain: str, projects: Sequence[str]) -> dict[str, object]:
        """The payload for one coding awakening.

        ``projects`` is the resolved project plus the repositories related to it, and **the first
        element is the session's repository**: the project context and the orientation map come
        from that one, while the episodes and the map list span the whole set, because the set
        widens the recall rather than moving the session. A set whose first element is a related
        repository therefore answers about that one instead, which is why the convention is stated
        here rather than left to be inferred.

        ``domain`` is not validated: it is the domain Munnin's ``awaken`` answered for a moment
        earlier, so an agent that does not exist has already failed one call, and a second check
        here would be a second opinion about a table this server does not own.
        """
        ordered = [project for project in projects if project]
        if not ordered:
            raise ValueError(
                "a coding awakening needs at least one project: the session's own repository"
            )
        repo = self._repository_for()

        context: list[dict[str, object]] = []
        for project in ordered:
            context += [
                _context_row(entry)
                for entry in repo.list_context_entries(project=project, agent=domain)
            ]

        maps: list[dict[str, object]] = []
        for project in ordered:
            found = repo.get_map(project=project)
            if found is not None:
                maps.append(_map_row(found))

        return {
            # the overlay's own lane, whole: it is what makes this an awakening for a coder
            "coding_lane": [_pattern_row(pattern) for pattern in repo.coding_lane()],
            # the set's episodes: the index to choose from, and the newest body per project, which
            # is where the open items a session carries forward actually live
            "episode_index": [
                _index_row(entry)
                for entry in repo.episode_index(agent=domain, projects=ordered)
            ],
            "episodes": [
                _episode_row(episode)
                for episode in repo.newest_episode_per_project(agent=domain, projects=ordered)
            ],
            # this server's own: the context index for the set, and one map per project that has
            # one, both in the set's order
            "context_index": context,
            "maps": maps,
        }


# --- the row shapers ------------------------------------------------------------------
#
# One shaper per entity, so the payload's field names live in a single place and the entities
# keep their own.


def _pattern_row(pattern: CodingPattern) -> dict[str, object]:
    return {
        "title": pattern.title,
        "tags": list(pattern.tags),
        "created_date": pattern.created_date,
        "modified_date": pattern.modified_date,
        "content": pattern.full_content,
    }


def _episode_row(episode: Episode) -> dict[str, object]:
    return {
        "project": episode.project,
        "title": episode.title,
        "created_date": episode.created_date,
        "modified_date": episode.modified_date,
        "content": episode.full_content,
    }


def _index_row(entry: EpisodeIndexEntry) -> dict[str, object]:
    return {
        "uuid": entry.uuid,
        "title": entry.title,
        "project": entry.project,
        "tags": list(entry.tags),
        "created_date": entry.created_date,
        "modified_date": entry.modified_date,
    }


def _context_row(entry: ContextEntry) -> dict[str, object]:
    """The index projection, never the body: a body arrives on relevance (decision 2)."""
    return {
        "project": entry.project,
        "scope": entry.scope.value,
        "theme": entry.theme,
        "description": entry.description,
        "tags": list(entry.tags),
        "created_date": entry.created_date,
        "modified_date": entry.modified_date,
    }


def _map_row(found: OrientationMap) -> dict[str, object]:
    """The map whole, dates included: the bare ``/map-orientation`` load does an mtime check."""
    return {
        "project": found.project,
        "description": found.description,
        "created_date": found.created_date,
        "modified_date": found.modified_date,
        "content": found.full_content,
    }
