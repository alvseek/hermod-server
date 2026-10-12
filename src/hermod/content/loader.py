"""Serves the overlay's procedures and templates, read live from the checkout.

A procedure is served as an MCP **Prompt** — the how-to an agent reads before acting — and a
template as an MCP **Resource**. Content is read from a source's checkout on every call, so
editing a procedure changes what is served with no re-import and no second copy to keep in step.

Composition is two substitutions, and the split follows the overlay's own layout: component
references are inlined (a carried copy — see ``inline.py``), then the ``## Storage Mechanics``
section is swapped for the backend document's concrete ops (imported — see ``seam_bridge.py``).
A served Prompt and an installed skill are therefore the same instruction, which is the property
this module exists to preserve.

**What is not served.** ``push-all`` and ``pull-all`` compose the memory store's push and pull,
and the memory write is durable on every insert, so there is nothing left for them to do here.
That list is the server's only say over the overlay's content, and it is a constant so that
adding to it is a visible one-liner rather than a branch somewhere.

**Sources are a list, and today it holds one.** The shape is deliberate rather than speculative:
a client that can connect only one server would want Munnin's procedures through this one, and
that arrives as a proxy provider *beside* this source, so nothing here changes when it does.

**The backend document is configuration.** A served client has no local store, so a served
prompt must compose onto the store's ops rather than the markdown ones. The default is the
markdown document while the project store's mechanics are unwritten; flipping it is a config
change, not a code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hermod.configuration.config import DEFAULT_BACKEND_DOC
from hermod.content.inline import inline_components
from hermod.content.seam_bridge import seam_compose

_PROCEDURES_DIR = "procedures"
_TEMPLATES_DIR = "templates"
_COMPONENTS_DIR = "components"
_ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"

# The server's only say over the overlay's content.
EXCLUDED_PROCEDURES = frozenset({"push-all", "pull-all"})

# `§ op` — the abstract op reference, and its definition in a backend section. Both patterns
# mirror the overlay compiler's, because they are the *reporting* half of the seam: the seam
# module extracts and substitutes sections, and says nothing about which ops a body asked for.
_OP_RE = re.compile(r"§\s*([a-z][a-z0-9-]*)")
_OP_DEF_RE = re.compile(r"^#{1,6}\s*§\s*([a-z][a-z0-9-]*)", re.MULTILINE)

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"\*\*|\*|`")
_OPENERS, _CLOSERS, _TERMINATORS = "([", ")]", ".!?"


@dataclass(frozen=True)
class ContentSource:
    """One place prompts and resources come from."""

    name: str
    root: Path


def _first_sentence(paragraph: str) -> str:
    """``paragraph`` up to its first sentence terminator, outside any parenthetical.

    A ``.``, ``!`` or ``?`` ends the sentence only when followed by whitespace or the end, and
    never inside ``(...)`` or ``[...]``: a question mark in a bracketed aside is part of the
    sentence, not the end of it.
    """
    depth = 0
    for i, ch in enumerate(paragraph):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
        elif ch in _TERMINATORS and depth == 0:
            if i + 1 == len(paragraph) or paragraph[i + 1].isspace():
                return paragraph[: i + 1]
    return paragraph


def _plain(text: str) -> str:
    """Markdown flattened to prose: a link becomes its label, emphasis is dropped."""
    return _MD_EMPHASIS.sub("", _MD_LINK.sub(r"\1", text))


def _lead_sentence(text: str) -> str:
    """The first sentence of a document's opening paragraph, as plain prose.

    Every procedure opens with its ``#`` title and then a paragraph whose first sentence says
    what the procedure is for; the storage-backend caveat is always a later sentence, so stopping
    at the first one drops the part no client needs. Returns ``""`` when there is no prose.
    """
    body = text.replace("\r\n", "\n")
    for block in body.split("\n\n"):
        paragraph = " ".join(block.split())
        if not paragraph or paragraph.startswith("#"):
            continue
        return _plain(_first_sentence(paragraph)).strip()
    return ""


def _h1_title(text: str) -> str:
    """The document's own ``#`` heading, as a display title.

    Derived rather than authored, so the two cannot drift: a title that reads badly is a signal
    to fix the heading, not to keep a second name beside it.
    """
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("# "):
            return _plain(line[2:]).strip()
    return ""


def _body_without_mechanics(text: str, marker: str) -> str:
    """The procedure text with its storage-mechanics section removed.

    Isolates the ops the *body* references from the ops the backend *defines*.
    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.strip() == marker), None)
    if start is None:
        return text
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].strip() == "---":
            end = j
            break
    return "".join(lines[:start] + lines[end:])


class ContentLoader:
    """Reads and composes served overlay content from one or more checkouts."""

    def __init__(
        self,
        sources: list[ContentSource] | tuple[ContentSource, ...],
        backend_doc: str = DEFAULT_BACKEND_DOC,
    ) -> None:
        self._sources = tuple(sources)
        self._backend_doc = backend_doc

    # --- sources ---------------------------------------------------------------

    def sources(self) -> tuple[ContentSource, ...]:
        """The configured sources, in precedence order."""
        return self._sources

    def available(self) -> bool:
        """True when every source's checkout is present.

        A missing checkout is not fatal: the loader serves nothing from it, so a server without
        content still boots. Composing a prompt that *is* found still raises, because a
        half-composed procedure is worse than none.
        """
        return bool(self._sources) and all(source.root.is_dir() for source in self._sources)

    def _backend_path(self) -> Path | None:
        """The configured backend document, from the first source that has it."""
        for source in self._sources:
            candidate = source.root / self._backend_doc
            if candidate.is_file():
                return candidate
        return None

    # --- prompts (procedures, composed) ----------------------------------------

    def _procedures(self) -> dict[str, Path]:
        """The served procedures, name to source file, discovered on every call.

        Read live, like the content itself: a file that appears is served, a file that goes is
        not, and neither needs a server edit. An absent checkout yields nothing rather than
        failing.
        """
        found: dict[str, Path] = {}
        for source in self._sources:
            directory = source.root / _PROCEDURES_DIR
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                if path.stem not in EXCLUDED_PROCEDURES:
                    found.setdefault(path.stem, path)
        return found

    def _procedure_path(self, name: str) -> Path:
        """The source file of a served procedure. Raises ``KeyError`` for any other name."""
        try:
            return self._procedures()[name]
        except KeyError:
            raise KeyError(f"unknown prompt: {name}") from None

    def list_prompts(self) -> list[str]:
        """The served procedure names."""
        return sorted(self._procedures())

    def title_prompt(self, name: str) -> str:
        """A display title for a served procedure, from its own ``#`` heading."""
        return _h1_title(self._procedure_path(name).read_text(encoding="utf-8")) or name

    def describe_prompt(self, name: str) -> str:
        """A one-line purpose for a served procedure, read from the procedure itself.

        Descriptions are what a client shows in its command menu, so procedures sharing one
        templated sentence leave the menu unable to tell them apart. Reading each procedure's own
        opening sentence fixes that and keeps the two from drifting.
        """
        lead = _lead_sentence(self._procedure_path(name).read_text(encoding="utf-8"))
        return lead or f"Procedure '{name}'."

    def get_prompt(self, name: str, arguments: str | None = None) -> str:
        """Return the composed procedure, with ``arguments`` filled in when one is given.

        Every procedure carries a ``$ARGUMENTS`` placeholder that an installed slash command
        fills from what the user typed; a served Prompt fills it the same way, so the two stay
        equivalent. Substitution happens **only when an argument is supplied**, which keeps a
        no-argument served Prompt identical to the command it mirrors.
        """
        text = self._compose_prompt(name)
        return text.replace(_ARGUMENTS_PLACEHOLDER, arguments) if arguments else text

    def _inlined(self, text: str) -> tuple[str, tuple[str, ...]]:
        """Inline every component reference; returns ``(text, components used)``."""
        for source in self._sources:
            components = source.root / _COMPONENTS_DIR
            if components.is_dir():
                text, used, _missing = inline_components(text, components)
                return text, tuple(dict.fromkeys(used))
        return text, ()

    def _compose_prompt(self, name: str) -> str:
        """Inline the components, then substitute the backend's ops for the mechanics marker.

        The backend body is this procedure's own ``## [procedure]`` section plus a
        ``## [component]`` section for each component inlined into it, so ops arriving via a
        component resolve without being restated under every caller. Falls back to the inlined
        core when the procedure has no marker or the backend defines nothing for it.
        """
        path = self._procedure_path(name)
        core, components = self._inlined(path.read_text(encoding="utf-8"))

        backend = self._backend_path()
        if backend is None:
            return core
        compose = seam_compose(str(path.parent.parent))
        try:
            section = compose.compose_backend_section(
                backend.read_text(encoding="utf-8"), name, components
            )
            composed = compose.substitute_storage_mechanics(core, section)
        except KeyError:
            return core
        return self._inlined(composed)[0]

    def unresolved_ops(self, name: str) -> list[str]:
        """Ops the composed prompt references that its backend section does not define.

        The seam substitutes a section and says nothing about what the body asked for, so this is
        the reporting half: a served prompt carrying an op nothing defines is a real defect with
        no user on this side to notice it.
        """
        source_text = self._procedure_path(name).read_text(encoding="utf-8")
        backend = self._backend_path()
        if backend is None:
            return []
        compose = seam_compose(str(self._procedure_path(name).parent.parent))
        marker = compose.STORAGE_MARKER
        referenced = set(_OP_RE.findall(_body_without_mechanics(source_text, marker)))
        if not referenced:
            return []
        _core, components = self._inlined(source_text)
        try:
            section = compose.compose_backend_section(
                backend.read_text(encoding="utf-8"), name, components
            )
        except KeyError:
            return sorted(referenced)
        return sorted(referenced - set(_OP_DEF_RE.findall(section)))

    # --- resources (templates, verbatim) ---------------------------------------

    def list_resources(self) -> list[str]:
        """The served resource names — the templates each source carries."""
        names: set[str] = set()
        for source in self._sources:
            directory = source.root / _TEMPLATES_DIR
            if directory.is_dir():
                names |= {p.stem for p in directory.glob("*.md")}
        return sorted(names)

    def _resource_path(self, name: str) -> Path:
        """The source file of a served resource. Raises ``KeyError`` if absent."""
        for source in self._sources:
            candidate = source.root / _TEMPLATES_DIR / f"{name}.md"
            if candidate.is_file():
                return candidate
        raise KeyError(f"unknown resource: {name}")

    def get_resource(self, name: str) -> str:
        """Return a template verbatim. Raises ``KeyError`` if absent."""
        return self._resource_path(name).read_text(encoding="utf-8")

    def title_resource(self, name: str) -> str:
        """A display title for a resource, from its own ``#`` heading."""
        return _h1_title(self.get_resource(name)) or name

    def describe_resource(self, name: str) -> str:
        """A one-line purpose for a resource, read from the template itself."""
        return _lead_sentence(self.get_resource(name)) or f"Template '{name}'."
