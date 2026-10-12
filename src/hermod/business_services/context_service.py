"""The project store's service layer — the ops the served ``db`` mechanics call.

Every op ``storage-backends/db.md`` names for ``update-project-context``, ``load-project-context``
and ``map-orientation`` has a method here, and the MCP face registers a tool per method, so a
served procedure that says "call § list-context-entries" resolves to something an agent can
actually call.

**Two ops exist only as text, and that is structural.** ``§ ensure-context-dir`` has no directory
to ensure and ``§ update-context-index`` projects a SELECT over the rows, so neither has code —
``db.md`` declares them no-ops, exactly as the memory core's ``db`` backend no-ops its own derived
index and directory steps. A third, ``§ check-entry-size``, *is* code because the store enforces it.

**The size cap is carried, not invented.** The markdown backend splits an entry that grows past
1000 lines into one file per sub-theme; the store has no second file to split into, so it refuses
the write instead. Same threshold, different remedy, because the medium differs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_repositories.project_repository import ProjectRepository

#: The markdown backend's split point, carried here as a refusal point.
MAX_ENTRY_LINES = 1000


@dataclass(frozen=True)
class SizeVerdict:
    """The answer ``§ check-entry-size`` gives: how big the entry is, and whether it is too big."""

    lines: int
    max_lines: int
    oversized: bool


class ContextService:
    """The ops over project context entries and the orientation map."""

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    # --- update-project-context / load-project-context ---

    def list_context_entries(
        self, *, project: str, scope: ContextScope | None = None
    ) -> Sequence[ContextEntry]:
        """The project's live entries, both scopes unless one is named. This *is* the context
        index: the markdown backend keeps a separate ``context-index.md``, and here the rows
        are the index, so ``§ update-context-index`` has nothing to write."""
        return self._repository.list_context_entries(project=project, scope=scope)

    def read_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> ContextEntry | None:
        """One entry whole, or ``None``."""
        return self._repository.get_context_entry(project=project, scope=scope, theme=theme)

    def create_context_entry(
        self,
        *,
        project: str,
        scope: ContextScope,
        theme: str,
        content: str,
        description: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> ContextEntry:
        """Create one entry. Refuses an oversized body (``§ check-entry-size``) and an identity
        that is already taken."""
        self._refuse_oversized(content)
        return self._repository.create_context_entry(
            ContextEntry(
                project=project,
                scope=scope,
                theme=theme,
                full_content=content,
                description=description,
                tags=list(tags or []),
            )
        )

    def update_context_entry(
        self,
        *,
        project: str,
        scope: ContextScope,
        theme: str,
        content: str,
        description: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> ContextEntry:
        """Replace a live entry's body, description and tags. Refuses an oversized body.

        A wholesale replace, matching the markdown backend's write-back of the whole entry:
        an omitted ``description`` or ``tags`` clears it rather than preserving the stored value.
        """
        self._refuse_oversized(content)
        return self._repository.update_context_entry(
            ContextEntry(
                project=project,
                scope=scope,
                theme=theme,
                full_content=content,
                description=description,
                tags=list(tags or []),
            )
        )

    def move_context_entry(
        self,
        *,
        project: str,
        theme: str,
        from_scope: ContextScope,
        to_scope: ContextScope,
    ) -> ContextEntry:
        """Change a live entry's scope, body untouched — the store's one-step form of the
        markdown backend's copy-then-delete."""
        return self._repository.move_context_entry(
            project=project, theme=theme, from_scope=from_scope, to_scope=to_scope
        )

    def delete_context_entry(self, *, project: str, scope: ContextScope, theme: str) -> None:
        """Tombstone one entry."""
        self._repository.delete_context_entry(project=project, scope=scope, theme=theme)

    def check_entry_size(self, *, content: str) -> SizeVerdict:
        """Measure an entry against the cap. A pure measurement: ``create`` and ``update`` are
        where it is enforced, so a caller can read the verdict before writing."""
        lines = len(content.splitlines())
        return SizeVerdict(
            lines=lines,
            max_lines=MAX_ENTRY_LINES,
            oversized=lines > MAX_ENTRY_LINES,
        )

    def _refuse_oversized(self, content: str) -> None:
        """``§ check-entry-size`` as a gate: raise rather than store an over-cap entry."""
        verdict = self.check_entry_size(content=content)
        if verdict.oversized:
            raise ValueError(
                f"entry is {verdict.lines} lines, over the {MAX_ENTRY_LINES}-line cap; split it "
                "into one entry per sub-theme and store each as its own theme"
            )

    # --- map-orientation ---

    def map_exists(self, *, project: str) -> bool:
        """Whether the project has an orientation map. The served ``§ map-exists``."""
        return self._repository.map_exists(project=project)

    def read_map(self, *, project: str) -> OrientationMap | None:
        """The project's map, whole, or ``None``. The served ``§ read-map``."""
        return self._repository.get_map(project=project)

    def create_map(
        self, *, project: str, content: str, description: str | None = None
    ) -> OrientationMap:
        """Insert the project's map. The served ``§ create-map``; refuses a second map."""
        return self._repository.create_map(
            OrientationMap(project=project, full_content=content, description=description)
        )

    def write_map(
        self, *, project: str, content: str, description: str | None = None
    ) -> OrientationMap:
        """Rewrite the project's map. The served ``§ write-map``."""
        return self._repository.write_map(
            OrientationMap(project=project, full_content=content, description=description)
        )
