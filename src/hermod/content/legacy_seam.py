# Legacy heading-based seam, copied from the pre-conversion content revision.
# It lives in the server so even a mixed legacy/Format 2 configuration treats all
# content checkouts as data. New Format 2 procedures use procedure-composer instead.

"""Seam substitution — the reference implementation.

Legacy memory procedures carry one ``## Storage Mechanics`` section; serving swaps it
for a backend's ``## [procedure]`` section. These are pure text operations.

A procedure may also inline **components** (shared fragments), and a component can
reference storage ops of its own. Those are defined once under the component's own
``## [component]`` section rather than repeated under every procedure that inlines it;
``compose_backend_section`` assembles the procedure's section plus its components' into
the single body that gets substituted. A backend may also open **every** composed
procedure with one ``## all-procedures`` section — prose the whole backend owes each
procedure, such as what a placeholder its ops use means — again written once.

This is the server's compatibility implementation for legacy roots, not the Format 2
composer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

STORAGE_MARKER = "## Storage Mechanics"

# A backend section that opens every composed procedure, when the backend defines it.
# It is prose, never ops: nothing in it can satisfy a `§` reference, and its presence
# never wires a procedure the backend does not otherwise name.
PREAMBLE_SECTION = "all-procedures"

# The `§` op sigil is KEPT in the composed output — it signals to the reader that a step
# was provided by the storage backend (a seam swap point), useful provenance.

# The composed procedure inlines the mechanics, so a "(see [Storage Mechanics](#…))"
# back-reference to the now-removed section is dangling + stale — drop the parenthetical;
# as a safety net, unlink any other reference to that anchor (keeping its text).
_SEAM_XREF = re.compile(r"[ \t]*\(see \[[^\]]*\]\(#storage-mechanics\)\)")
_SEAM_ANCHOR = re.compile(r"\[([^\]]*)\]\(#storage-mechanics\)")


def extract_section(doc: str, title: str) -> str:
    """Return the body under ``## {title}`` up to the next ``## `` header or EOF.

    Matches the level-2 header exactly. Raises ``KeyError`` if absent.
    """
    lines = doc.splitlines(keepends=True)
    header = f"## {title}"
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        raise KeyError(f"section not found: {header}")
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "".join(lines[start:end]).strip("\n")


def defines_section(doc: str, title: str) -> bool:
    """True when ``doc`` carries a ``## {title}`` header."""
    header = f"## {title}"
    return any(line.strip() == header for line in doc.splitlines())


def has_seam(doc: str) -> bool:
    """True when ``doc`` carries the seam as an actual ``## Storage Mechanics`` header —
    not merely the phrase in prose, as the backend contract README does."""
    return any(line.strip() == STORAGE_MARKER for line in doc.splitlines())


def compose_backend_section(
    doc: str, procedure: str, components: Sequence[str] = ()
) -> str:
    """The backend body for one compiled procedure: its own ``## {procedure}`` section
    followed by a ``## {component}`` section for each component inlined into it — and,
    when the backend defines ``## all-procedures``, that section ahead of both.

    A component's ops live **once**, under the component's own name, instead of being
    repeated under every procedure that inlines it — the same single-home rule the seam
    itself follows. Order is preamble first, then the procedure, then components as they
    were inlined; repeats and components the backend says nothing about are skipped.

    The preamble is prose the backend owes every procedure (what a placeholder in its
    ops means, say), so it is prepended only once a procedure is otherwise covered: a
    backend that defines the preamble and nothing else still defines nothing for this
    procedure, and ``KeyError`` is raised exactly as before.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for title in (procedure, *components):
        if title in seen:
            continue
        seen.add(title)
        try:
            parts.append(extract_section(doc, title))
        except KeyError:
            continue
    if not parts:
        raise KeyError(f"backend defines no section for: {procedure}")
    try:
        parts.insert(0, extract_section(doc, PREAMBLE_SECTION))
    except KeyError:
        pass  # this backend owes its procedures no preamble
    return "\n\n".join(parts)


def substitute_storage_mechanics(core: str, backend_section: str) -> str:
    """Replace the core's ``## Storage Mechanics`` section with ``backend_section``.

    The seam scaffolding is trimmed from the result: the ``## Storage Mechanics`` marker
    header is **dropped** (the backend section brings its own headers) and the now-stale
    ``(see [Storage Mechanics](#…))`` back-reference is dropped (the mechanics are inlined
    here). The ``§`` op sigils are **kept** — they signal to the reader that a step is
    storage-backend-provided. So the composed procedure reads as plain content, the same
    on both faces (installed markdown command and Munnin-served prompt). Everything after
    the section up to the next ``## `` header or standalone rule is preserved (so a
    trailing footer note survives). Raises ``KeyError`` if the marker is absent.
    """
    lines = core.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == STORAGE_MARKER:
            start = i
            break
    if start is None:
        raise KeyError("no '## Storage Mechanics' marker in procedure")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        # The mechanics body ends at the next section header or a standalone rule,
        # so trailing content (e.g. a footer note after the section) is preserved.
        if lines[j].startswith("## ") or lines[j].strip() == "---":
            end = j
            break
    head = "".join(lines[:start]).rstrip("\n")  # drop the marker header line itself
    tail = "".join(lines[end:])
    body = backend_section.strip("\n")
    composed = f"{head}\n\n{body}\n\n{tail}" if tail.strip() else f"{head}\n\n{body}\n"
    composed = _SEAM_XREF.sub("", composed)
    return _SEAM_ANCHOR.sub(r"\1", composed)
