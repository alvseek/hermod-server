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

**The template stamp is a gate, not content.** Every write that supplies a whole document must
declare the stamp its served template carries, so a caller that never read the template cannot
write one. The stamp is never stored, which is the memory core's own rule applied to a store that
keeps whole documents rather than blocks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_repositories.project_repository import ProjectRepository

#: The markdown backend's split point, carried here as a refusal point.
MAX_ENTRY_LINES = 1000

#: The served template each document-supplying write must have read. Keys are the loader's
#: resource names, because those are what a caller is told to read.
_ENTRY_TEMPLATE = "project-context-template"
_MAP_TEMPLATE = "orientation-map-template"


@dataclass(frozen=True)
class SizeVerdict:
    """The answer ``§ check-entry-size`` gives: how big the entry is, and whether it is too big."""

    lines: int
    max_lines: int
    oversized: bool


class ContextService:
    """The ops over project context entries and the orientation map."""

    def __init__(
        self,
        repository_for: ProjectRepository | Callable[[], ProjectRepository],
        template_versions: Mapping[str, str | None] | None = None,
    ) -> None:
        # A repository, or a factory for one. The server passes a factory, because the tenant is a
        # property of the *call* and not of the process: the op that runs is the only place a
        # verified token exists, so the repository cannot be built before it. A repository is still
        # accepted because a caller that already knows whose data it is (a migration, a test) has
        # nothing to defer.
        self._repository_for: Callable[[], ProjectRepository] = (
            repository_for if callable(repository_for) else (lambda: repository_for)
        )
        #: Served resource name -> the stamp it carries. Built from the loader, so the expected
        #: stamp is always the template's own and never a second copy that could drift from it.
        self._template_versions: dict[str, str | None] = dict(template_versions or {})

    def _repo(self) -> ProjectRepository:
        """The repository for the call in progress, built once per op."""
        return self._repository_for()

    # --- update-project-context / load-project-context ---

    def list_context_entries(
        self, *, project: str, scope: ContextScope | None = None
    ) -> Sequence[ContextEntry]:
        """The project's live entries, both scopes unless one is named. This *is* the context
        index: the markdown backend keeps a separate ``context-index.md``, and here the rows
        are the index, so ``§ update-context-index`` has nothing to write."""
        return self._repo().list_context_entries(project=project, scope=scope)

    def read_context_entry(
        self, *, project: str, scope: ContextScope, theme: str
    ) -> ContextEntry | None:
        """One entry whole, or ``None``."""
        return self._repo().get_context_entry(project=project, scope=scope, theme=theme)

    def create_context_entry(
        self,
        *,
        project: str,
        scope: ContextScope,
        theme: str,
        content: str,
        template_version: str,
        description: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> ContextEntry:
        """Create one entry. Refuses an oversized body (``§ check-entry-size``), a missing or stale
        template stamp (``§ template``), and an identity that is already taken."""
        self._refuse_oversized(content)
        self._refuse_stale_template(_ENTRY_TEMPLATE, template_version)
        return self._repo().create_context_entry(
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
        template_version: str,
        description: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> ContextEntry:
        """Replace a live entry's body, description and tags. Refuses an oversized body, and a
        missing or stale template stamp — it restates the whole document, so it carries the same
        gate the create does.

        A wholesale replace, matching the markdown backend's write-back of the whole entry:
        an omitted ``description`` or ``tags`` clears it rather than preserving the stored value.
        """
        self._refuse_oversized(content)
        self._refuse_stale_template(_ENTRY_TEMPLATE, template_version)
        return self._repo().update_context_entry(
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
        return self._repo().move_context_entry(
            project=project, theme=theme, from_scope=from_scope, to_scope=to_scope
        )

    def delete_context_entry(self, *, project: str, scope: ContextScope, theme: str) -> None:
        """Tombstone one entry."""
        self._repo().delete_context_entry(project=project, scope=scope, theme=theme)

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

    def _refuse_stale_template(self, template: str, declared: str | None) -> None:
        """``§ template``'s gate: a document write must declare the served template's stamp.

        Three refusals, named separately because they send the caller to different places: no
        expected stamp means the template is not served at all, a missing declared stamp means the
        caller never read it, and a mismatch means it read a different one.
        """
        expected = self._template_versions.get(template)
        if expected is None:
            raise ValueError(
                f"template {template!r} is not served, so its stamp cannot be checked; the server "
                "needs the overlay checkout that carries it"
            )
        if not declared:
            raise ValueError(
                f"template_version is required: read {template!r} from resources and pass the "
                f"stamp it carries below its title ({expected!r})"
            )
        if declared != expected:
            raise ValueError(
                f"template_version {declared!r} does not match the served {template!r} "
                f"({expected!r}); read the template again and declare its current stamp"
            )

    # --- map-orientation ---

    def map_exists(self, *, project: str) -> bool:
        """Whether the project has an orientation map. The served ``§ map-exists``."""
        return self._repo().map_exists(project=project)

    def read_map(self, *, project: str) -> OrientationMap | None:
        """The project's map, whole, or ``None``. The served ``§ read-map``."""
        return self._repo().get_map(project=project)

    def create_map(
        self,
        *,
        project: str,
        content: str,
        template_version: str,
        description: str | None = None,
    ) -> OrientationMap:
        """Insert the project's map. The served ``§ create-map``; refuses a second map, and a
        missing or stale template stamp."""
        self._refuse_stale_template(_MAP_TEMPLATE, template_version)
        return self._repo().create_map(
            OrientationMap(project=project, full_content=content, description=description)
        )

    def write_map(
        self,
        *,
        project: str,
        content: str,
        template_version: str,
        description: str | None = None,
    ) -> OrientationMap:
        """Rewrite the project's map. The served ``§ write-map``; refuses a missing or stale
        template stamp, since it restates the whole document."""
        self._refuse_stale_template(_MAP_TEMPLATE, template_version)
        return self._repo().write_map(
            OrientationMap(project=project, full_content=content, description=description)
        )
