"""Hermod's entities: the project-keyed memory the overlay's ``db`` backend defers to.

Two tables, two shapes:

``ContextEntry``
    One project-context entry, addressed by ``(project, scope, theme)``. ``theme`` is the
    markdown backend's filename stem, so an entry keeps its identity when it moves between
    scopes and only ``scope`` changes. ``description`` and ``tags`` are the frontmatter the
    context index line renders; ``full_content`` is the whole document.
``OrientationMap``
    The project's orientation map, one per project, stored whole — the file the markdown
    backend would have written, frontmatter included.

``scope`` is the axis Hermod owns: ``shared`` is the cross-agent layer, ``private`` the one
agent's. Munnin's ``project`` column is reserved and unused, which is why project-keyed
memory lives here rather than there (ADR-023).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ContextScope(str, Enum):
    """The two layers the overlay splits a project's context into."""

    shared = "shared"
    private = "private"


@dataclass(kw_only=True)
class ContextEntry:
    """One project-context entry — the shared or private knowledge a project carries.

    ``id`` is the store's own rowid and never leaves it; the entry's identity is the
    ``(project, scope, theme)`` triple, which is what the markdown backend expresses as a
    file path. A set ``deleted_date`` means tombstoned and excluded from every read."""

    project: str
    scope: ContextScope
    theme: str
    full_content: str
    id: int | None = None
    description: str | None = None
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
    description: str | None = None
    created_date: str | None = None
    modified_date: str | None = None
