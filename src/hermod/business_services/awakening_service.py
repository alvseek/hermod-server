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

from hermod.business_services.episode_page import (
    DEFAULT_MAX_BODY_BYTES,
    EpisodePage,
    page_body,
)
from hermod.data_entities.awakening import CodingPattern, Episode, EpisodeIndexEntry
from hermod.data_entities.project_context import ContextEntry, OrientationMap
from hermod.data_repositories.project_repository import ProjectRepository

#: How many timestamp groups one project's episode body carries in the awakening. The same
#: bounded starting context Munnin's chat awakening uses: enough to pick up where the last
#: session stopped, with a cursor when the record holds more.
AWAKEN_BLOCK_LIMIT = 3


class AwakeningService:
    """The coding half of an awakening, in one read-only call."""

    def __init__(
        self,
        repository_for: Callable[[], ProjectRepository],
        *,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        # A factory for the same reason ContextService takes one: the tenant is a property of the
        # verified token on *this* call, never of the process.
        if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int):
            raise ValueError("max_body_bytes must be a whole number of bytes")
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive number of bytes")
        self._repository_for = repository_for
        self._max_body_bytes = max_body_bytes

    def awaken(self, *, domain: str, projects: Sequence[str]) -> dict[str, object]:
        """The payload for one coding awakening.

        ``projects`` is the resolved project plus the repositories related to it, and **the first
        element is the session's repository**. That element is the session's home, not a filter:
        every list the payload carries spans the whole set, because the set widens the recall
        rather than moving the session. A set whose first element is a related repository therefore
        names that one as the home, which is why the convention is stated here rather than left to
        be inferred.

        ``domain`` is not validated: it is the domain Munnin's ``awaken`` answered for a moment
        earlier, so an agent that does not exist has already failed one call, and a second check
        here would be a second opinion about a table this server does not own.
        """
        ordered = list(projects)
        # An empty element is refused rather than filtered out: `projects[0]` *is* the session's
        # repository, so quietly dropping a blank would answer about a different one.
        if not ordered or any(not project for project in ordered):
            raise ValueError(
                "a coding awakening needs at least one project, and every entry must name one: "
                "the first is the session's own repository"
            )
        repo = self._repository_for()

        context: list[dict[str, object]] = []
        for project in ordered:
            # Both layers, in the order the single call used to return them: the shared one first,
            # then this agent's own. A layer is a separate op now, which is what taking the scope
            # argument off the interface costs here.
            context += [
                _context_row(entry) for entry in repo.list_shared_context(project=project)
            ]
            context += [
                _context_row(entry)
                for entry in repo.list_agent_context(project=project, agent=domain)
            ]

        maps: list[dict[str, object]] = []
        for project in ordered:
            found = repo.get_map(project=project)
            if found is not None:
                maps.append(_map_row(found))

        return {
            # the overlay's own lane, whole: it is what makes this an awakening for a coder
            "coding_lane": [_pattern_row(pattern) for pattern in repo.coding_lane()],
            # the set's episodes: the index to choose from, and the newest page per project, which
            # is where the open items a session carries forward actually live. A page, not a body:
            # each row names its uuid and cursor so the rest is one Munnin `get` away, and the
            # two-call awakening stays two calls.
            "episode_index": [
                _index_row(entry) for entry in repo.episode_index(agent=domain, projects=ordered)
            ],
            "episodes": [
                _episode_row(
                    episode,
                    page_body(
                        episode.full_content or "",
                        limit=AWAKEN_BLOCK_LIMIT,
                        max_body_bytes=self._max_body_bytes,
                    ),
                )
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


def _episode_row(episode: Episode, page: EpisodePage) -> dict[str, object]:
    """One project's newest episode as a page, plus the uuid that makes it continuable.

    ``uuid`` is not decoration: the page's content is deliberately partial, and the only way to
    ask for the next timestamp group is Munnin's ``get(uuid, cursor=…)``. A row without it hands
    a caller a body it cannot finish reading."""
    return {
        "uuid": episode.uuid,
        "project": episode.project,
        "title": episode.title,
        "created_date": episode.created_date,
        "modified_date": episode.modified_date,
        **page.fields(),
        "content": page.content,
    }


def _index_row(entry: EpisodeIndexEntry) -> dict[str, object]:
    return {
        "uuid": entry.uuid,
        "title": entry.title,
        "content_summary": entry.content_summary,
        "project": entry.project,
        "tags": list(entry.tags),
        "created_date": entry.created_date,
        "modified_date": entry.modified_date,
    }


def _context_row(entry: ContextEntry) -> dict[str, object]:
    """The index projection, never the body: a body arrives on relevance (decision 2).

    ``uuid`` is what makes the row addressable: a body is read, edited and retired by that value
    through the memory layer, so an index without it hands a caller rows it cannot act on.
    """
    return {
        "uuid": entry.uuid,
        "project": entry.project,
        "scope": entry.scope.value,
        "theme": entry.theme,
        "content_summary": entry.content_summary,
        "tags": list(entry.tags),
        "created_date": entry.created_date,
        "modified_date": entry.modified_date,
    }


def _map_row(found: OrientationMap) -> dict[str, object]:
    """The map whole, dates included: the bare ``/map-orientation`` load does an mtime check."""
    return {
        "project": found.project,
        "content_summary": found.content_summary,
        "created_date": found.created_date,
        "modified_date": found.modified_date,
        "content": found.full_content,
    }
