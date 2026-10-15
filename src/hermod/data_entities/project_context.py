"""Hermod's entities: the project-keyed memory the overlay's ``db`` backend defers to.

Two tables, two shapes:

``ContextEntry``
    One project-context entry, addressed by its ``uuid``, which the store assigns at insert.
    ``theme`` is the markdown backend's filename stem and is now only a display name: the
    theme is how an agent recognizes which row it means in a listing, never how the store is
    addressed. ``content_summary`` and ``tags`` are the one-line summary and the tags the
    frontmatter carries; ``full_content`` is the whole document.
``OrientationMap``
    The project's orientation map, one per project, stored whole — the file the markdown
    backend would have written, frontmatter included.

``scope`` is the axis Hermod owns: ``shared`` is the cross-agent layer, ``agent`` the one
agent's. Munnin's ``project`` column is reserved and unused, which is why project-keyed
memory lives here rather than there (ADR-023).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ContextScope(str, Enum):
    """The two layers the overlay splits a project's context into."""

    shared = "shared"
    agent = "agent"


@dataclass(kw_only=True)
class ContextEntry:
    """One project-context entry — the shared or agent-owned knowledge a project carries.

    ``id`` is the store's own rowid and never leaves it. ``uuid`` is the entry's identity,
    assigned at insert, and it is what a caller addresses it by afterwards. ``theme`` is the
    markdown backend's filename stem, and a display name only. A row with ``deleted_date`` or
    ``archived_date`` set is retired, and every read excludes it."""

    project: str
    scope: ContextScope
    theme: str
    full_content: str
    uuid: str | None = None
    id: int | None = None
    content_summary: str | None = None
    tags: list[str] = field(default_factory=list)
    created_date: str | None = None
    modified_date: str | None = None
    deleted_date: str | None = None


@dataclass(kw_only=True)
class OrientationMap:
    """One project's orientation map, whole.

    ``project`` is the key: a project has at most one map, so there is no scope, theme or
    lifecycle column. ``write_map`` replaces it wholesale rather than retiring it, exactly
    as the markdown backend rewrites the file."""

    project: str
    full_content: str
    id: int | None = None
    content_summary: str | None = None
    created_date: str | None = None
    modified_date: str | None = None
