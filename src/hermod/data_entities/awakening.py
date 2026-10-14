"""The two Munnin-owned rows the coding awakening reads.

Neither of these is project context, which is why they are not in ``project_context.py``:
Munnin owns them, writes them and retires them, and Hermod reads them for exactly one
purpose (ADR-029). A coding awakening needs the agent's newest episode per repository and
the fleet's coding-reasoning lane; nothing here is created, updated or deleted by this
server.

``Episode``
    One of an agent's episodic records. It is agent-scoped and carries the ``project`` that
    selects it for one repository, so the coding awakening gets one per project in the set.
``CodingPattern``
    One row of the coding lane: shared memory with no project and no owner, which is what
    keeps the lane out of every other agent's awakening.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class Episode:
    """One episodic record, whole.

    ``id`` is the store's own rowid and never leaves it. ``project`` is nullable in the table
    and non-null for every row the awakening reads, because the read selects by it: an
    episode with no project belongs to no repository and is not carried forward for one."""

    title: str
    full_content: str
    project: str | None = None
    agent: str | None = None
    id: int | None = None
    tags: list[str] = field(default_factory=list)
    created_date: str | None = None
    modified_date: str | None = None


@dataclass(kw_only=True)
class EpisodeIndexEntry:
    """One line of the episodic index: enough to choose an episode, and no body.

    The same projection Munnin's own index carries — ``uuid``, title, content summary, project
    and dates — so a caller that holds either index selects from it the same way, and a body it
    does not carry is one ``get`` away."""

    uuid: str
    title: str
    created_date: str | None = None
    modified_date: str | None = None
    project: str | None = None
    content_summary: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class CodingPattern:
    """One pattern from the coding-reasoning lane, whole.

    The lane is fleet memory: shared, project-less, and owned by nobody, so a pattern has no
    scope, no agent and no project — only its uuid in the store and its title."""

    title: str
    full_content: str
    id: int | None = None
    tags: list[str] = field(default_factory=list)
    created_date: str | None = None
    modified_date: str | None = None
