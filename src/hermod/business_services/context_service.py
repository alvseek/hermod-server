"""The project store's service layer — the ops the served ``db`` mechanics call.

Discovery and creation are this layer's: the two lists, the two creates, the two direction ops
and the map's three. Everything addressed by a uuid that already exists is the memory layer's
own, named by ``db.md``'s op definitions rather than implemented here, which is why no method
below writes a body, a metadata field or a lifecycle flag.

**Three ops exist only as text, and that is structural.** ``§ ensure-context-dir`` has no
directory to ensure, ``§ update-context-index`` projects a SELECT over the rows, and
``§ check-entry-size`` measures a document the create already refuses when it is too big, so
``db.md`` declares all three no-ops — the same resolution the memory core's ``db`` backend gives
its own derived index and directory steps.

**The size cap is carried, not invented, and it gates creation.** The markdown backend splits an
entry that grows past 1000 lines into one file per sub-theme; the store has no second file to
split into, so the create refuses the document instead. Same threshold, different remedy, because
the medium differs, and it is applied where this store is the writer: a later body edit belongs to
the memory layer, which knows no cap.

**The template stamp is a gate, not content, and it also gates creation.** A write that supplies a
whole document must declare the stamp its served template carries, so a caller that never read the
template cannot write one. The stamp is never stored, which is the memory core's own rule applied
to a store that keeps whole documents rather than blocks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_repositories.project_repository import ProjectRepository

#: The markdown backend's split point, carried here as a refusal point on creation.
MAX_ENTRY_LINES = 1000

#: The served template each document-supplying write must have read. Keys are the loader's
#: resource names, because those are what a caller is told to read.
_ENTRY_TEMPLATE = "project-context-template"
_MAP_TEMPLATE = "orientation-map-template"


class ContextService:
    """The ops over project context entries and the orientation map."""

    def __init__(
        self,
        repository_for: Callable[[], ProjectRepository],
        template_versions: Mapping[str, str | None] | None = None,
    ) -> None:
        # A factory, not a repository: the tenant is a property of the *call* and not of the
        # process, so the op that runs is the only place a verified token exists and the repository
        # cannot be built before it.
        self._repository_for = repository_for
        #: Served resource name -> the stamp it carries. Built from the loader, so the expected
        #: stamp is always the template's own and never a second copy that could drift from it.
        self._template_versions: dict[str, str | None] = dict(template_versions or {})

    def _repo(self) -> ProjectRepository:
        """The repository for the call in progress, built once per op."""
        return self._repository_for()

    # --- load-project-context / update-project-context ---

    def list_shared_context(self, *, project: str) -> Sequence[ContextEntry]:
        """The project's live shared entries, in theme order.

        This *is* half of the context index: the markdown backend keeps a separate
        ``context-index.md``, and here the rows are the index, so ``§ update-context-index`` has
        nothing to write.
        """
        return self._repo().list_shared_context(project=project)

    def list_agent_context(self, *, project: str, agent: str) -> Sequence[ContextEntry]:
        """One agent's live entries for the project.

        ``agent`` is required rather than optional: it owns those rows, and the token names the
        person rather than the agent, so it cannot be derived from the call.
        """
        return self._repo().list_agent_context(project=project, agent=agent)

    def create_shared_context_entry(
        self,
        *,
        project: str,
        theme: str,
        content: str,
        template_version: str,
        content_summary: str,
        tags: Sequence[str] | None = None,
    ) -> ContextEntry:
        """Create one shared entry.

        Refuses an oversized body (``§ check-entry-size``), a blank summary, and a missing or
        stale stamp (``§ template``). No theme is checked, so deciding between creating and
        updating belongs to the caller that read the listing.
        """
        return self._create_entry(
            project=project,
            agent=None,
            scope=ContextScope.shared,
            theme=theme,
            content=content,
            template_version=template_version,
            content_summary=content_summary,
            tags=tags,
        )

    def create_agent_context_entry(
        self,
        *,
        project: str,
        agent: str,
        theme: str,
        content: str,
        template_version: str,
        content_summary: str,
        tags: Sequence[str] | None = None,
    ) -> ContextEntry:
        """Create one agent's entry. Same gates as the shared create, and the owner is part of
        the call because the row carries it."""
        return self._create_entry(
            project=project,
            agent=agent,
            scope=ContextScope.agent,
            theme=theme,
            content=content,
            template_version=template_version,
            content_summary=content_summary,
            tags=tags,
        )

    def _create_entry(
        self,
        *,
        project: str,
        agent: str | None,
        scope: ContextScope,
        theme: str,
        content: str,
        template_version: str,
        content_summary: str,
        tags: Sequence[str] | None,
    ) -> ContextEntry:
        """The two creates differ by the owner alone, so the gates and the shape live once."""
        self._refuse_oversized(content)
        self._refuse_blank_summary(content_summary)
        self._refuse_stale_template(_ENTRY_TEMPLATE, template_version)
        entry = ContextEntry(
            project=project,
            scope=scope,
            theme=theme,
            full_content=content,
            content_summary=content_summary,
            tags=list(tags or []),
        )
        repo = self._repo()
        return (
            repo.create_agent_context_entry(entry, agent=agent)
            if scope is ContextScope.agent
            else repo.create_shared_context_entry(entry)
        )

    def promote_context_entry(self, *, uuid: str) -> ContextEntry:
        """Move one entry into the shared layer, keeping its uuid. The served ``§ promote``."""
        return self._repo().promote_context_entry(uuid=uuid)

    def demote_context_entry(self, *, uuid: str, agent: str) -> ContextEntry:
        """Move one entry into an agent's layer, keeping its uuid. The served ``§ demote``."""
        return self._repo().demote_context_entry(uuid=uuid, agent=agent)

    def _refuse_oversized(self, content: str) -> None:
        """``§ check-entry-size`` as a gate, and the only place the cap is measured.

        Nothing reads a measurement back: the served step resolves to no action on this backend,
        so the write either lands or names the count and the cap in its refusal.
        """
        lines = len(content.splitlines())
        if lines > MAX_ENTRY_LINES:
            raise ValueError(
                f"entry is {lines} lines, over the {MAX_ENTRY_LINES}-line cap; split it into one "
                "entry per sub-theme and store each as its own theme"
            )

    def _refuse_blank_summary(self, content_summary: str) -> None:
        """A create must state the one line a listing shows beside the theme.

        Blank is refused rather than stored, the shape the memory layer's own insert uses: an
        entry nobody can tell apart from its neighbours is what the requirement exists to
        prevent, and the wording of the field is the document's own frontmatter line.
        """
        if not content_summary.strip():
            raise ValueError(
                "content_summary is required: pass the one line the listing shows beside the "
                "theme, taken from the document's frontmatter"
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

    def read_map(self, *, project: str) -> OrientationMap | None:
        """The project's map, whole, or ``None``. The served ``§ read-map``.

        It answers the existence question too, because an absent map comes back as ``None``, so
        no separate check is served.
        """
        return self._repo().get_map(project=project)

    def create_map(
        self,
        *,
        project: str,
        content: str,
        template_version: str,
        content_summary: str | None = None,
    ) -> OrientationMap:
        """Insert the project's map, assigning its uuid. The served ``§ create-map``; refuses a
        second map, and a missing or stale template stamp."""
        self._refuse_stale_template(_MAP_TEMPLATE, template_version)
        return self._repo().create_map(
            OrientationMap(project=project, full_content=content, content_summary=content_summary)
        )

    def update_map(
        self,
        *,
        project: str,
        content: str,
        template_version: str,
        content_summary: str | None = None,
    ) -> OrientationMap:
        """Rewrite the project's map. The served ``§ update-map``; refuses a missing or stale
        template stamp, since it restates the whole document, and a project with no map."""
        self._refuse_stale_template(_MAP_TEMPLATE, template_version)
        return self._repo().update_map(
            OrientationMap(project=project, full_content=content, content_summary=content_summary)
        )
