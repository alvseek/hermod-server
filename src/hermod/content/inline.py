"""The overlay's component inliner, carried rather than imported.

**This is a copy, and one test is what makes it safe.** The overlay keeps ``inline_components``
and ``component_body`` inside ``setup-scripts/compile-procedures.py`` — a CLI script, not a
module — so there is nothing to import the way the seam is imported. The copy exists so that a
served Prompt and an installed skill inline identically; when the overlay's inliner changes and
this one does not, ``tests/test_fidelity.py`` turns red instead of letting the two drift
quietly.

Two rules here are load-bearing, and each has its own test:

- a component's body is everything **after its first standalone ``---`` rule**, so the header
  block a component opens with never reaches the wire;
- only **component** links are de-linked. Any other link on the same line survives, because the
  overlay's own in-document anchor has to keep working in an installed skill.

The functions below are byte-for-byte the overlay's, deliberately: a rewrite would be a second
interpretation of the same rules, which is the drift this copy already risks once.
"""

from __future__ import annotations

import re
from pathlib import Path

# A component reference: `[label](<any-prefix>/components/<name>.md)`. The path prefix is free
# (a relative dev-time link and a placeholder-rooted one both resolve to the same component) and
# the closing paren is required, so a bare mention of a components/ path in prose is not a
# reference. Group 1 is the label that replaces the link; group 2 is the component name.
_COMPONENT_LINK = re.compile(r"\[([^\]]*)\]\([^)\n]*/components/([a-z-]+)\.md\)")


def _lines(text: str) -> list[str]:
    """Split into lines the way the overlay's compiler does.

    Deliberately not ``str.splitlines()``: that also splits on \\v, \\f and U+2028, which appear
    inside procedure prose and would introduce line breaks the overlay's compiler never made.
    """
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _emit(lines: list[str]) -> str:
    """Join printed lines the way the overlay's compiler does — one trailing newline each."""
    return "".join(line + "\n" for line in lines)


def component_body(text: str) -> list[str]:
    """The inlinable body of a component: everything after its first standalone ``---`` rule.

    A component opens with a header block (title plus a note that it is a component, not a
    standalone skill) separated from the body by that rule. A component with no such rule
    contributes nothing.
    """
    out: list[str] = []
    started = False
    for line in _lines(text):
        if started:
            out.append(line)
        if re.fullmatch(r"---[ \t]*", line):
            started = True
    return out


def inline_components(text: str, components_dir: Path) -> tuple[str, list[str], list[str]]:
    """Inline every component reference at its reference point.

    Returns ``(text, used, missing)``. On a referencing line each component link is replaced by
    its label text — so caller params written into the sentence survive — and the component body
    is inserted right after, preceded by a blank line.

    A missing component contributes no body but its name is returned in ``missing``, so the
    caller can surface it rather than letting the reference vanish unnoticed.
    """
    out: list[str] = []
    used: list[str] = []
    missing: list[str] = []

    for line in _lines(text):
        matches = _COMPONENT_LINK.findall(line)
        if not matches:
            out.append(line)
            continue

        out.append(_COMPONENT_LINK.sub(r"\1", line))
        for _label, name in matches:
            out.append("")
            path = components_dir / f"{name}.md"
            if path.is_file():
                used.append(name)
                out.extend(component_body(path.read_text(encoding="utf-8")))
            else:
                missing.append(name)

    return _emit(out), used, missing
