"""Fidelity: the carried half of composition still matches the overlay's compiler.

Composition is two substitutions. The seam is *imported* from the overlay, so it cannot drift.
The inliner is *carried*, so it can — and this is the check that catches it.

What is compared is **mechanic accounting**, not the composed text: the two sides deliberately
compose different backend documents once the project store exists (the compiler writes the
markdown mechanics into an installed command, the server writes the store's ops into a served
prompt). So the check pins the three things that must match whatever backend each side uses:

1. **the components inlined** — the compiler reports them; the loader exposes them;
2. **the ops the body references** — read out of each side's composed text, with the mechanics
   section stripped, so the backend's own additions cannot mask a body that changed;
3. **that a section was substituted at all** — the marker is gone from both.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hermod.content import loader as loader_mod
from hermod.content.loader import _OP_RE, ContentLoader, ContentSource, _body_without_mechanics

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "agent-memory-project"


def _overlay_compiler():
    """The overlay's own compiler, loaded by file path — the same way the server loads the seam."""
    spec = importlib.util.spec_from_file_location(
        "overlay_compile", CONTENT / "setup-scripts" / "compile-procedures.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["overlay_compile"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def loader() -> ContentLoader:
    return ContentLoader([ContentSource(name="project", root=CONTENT)])


@pytest.fixture(scope="module")
def compiler_reports(tmp_path_factory) -> dict:
    """One report per served procedure, from the overlay's own compile run."""
    compiler = _overlay_compiler()
    out = tmp_path_factory.mktemp("compiled")
    reports = {}
    for name in ContentLoader([ContentSource(name="project", root=CONTENT)]).list_prompts():
        src = CONTENT / "procedures" / f"{name}.md"
        reports[name] = compiler.compile_one(src, out, CONTENT)
    return reports


def test_the_same_components_are_inlined(loader: ContentLoader, compiler_reports: dict) -> None:
    """The carried inliner must pull in exactly what the overlay's own compiler pulls in."""
    for name, report in compiler_reports.items():
        assert loader.components_used(name) == report.components, name
    # Without this the comparison could pass while proving nothing: every procedure here would
    # have to inline nothing, and a silent inliner would look identical to a faithful one.
    compared = [name for name, report in compiler_reports.items() if report.components]
    assert compared, "no procedure inlined a component, so nothing was actually compared"


def test_the_same_ops_are_referenced(loader: ContentLoader, compiler_reports: dict) -> None:
    """The bodies agree on which ops they ask for, whatever backend each side composed onto."""
    compiler = _overlay_compiler()
    marker = compiler._seam.STORAGE_MARKER
    for name, report in compiler_reports.items():
        served = loader.get_prompt(name)
        body_ops = sorted(set(_OP_RE.findall(_body_without_mechanics(served, marker))))
        assert body_ops == report.referenced_ops, name


def test_both_substitute_a_mechanics_section(loader: ContentLoader, compiler_reports: dict) -> None:
    compiler = _overlay_compiler()
    marker = compiler._seam.STORAGE_MARKER
    for name in compiler_reports:
        served = loader.get_prompt(name)
        # A procedure with no marker keeps none; one with a marker must have had it swapped.
        if marker in (CONTENT / "procedures" / f"{name}.md").read_text(encoding="utf-8"):
            assert marker not in served, name


def test_no_served_prompt_leaves_an_op_unresolved(
    loader: ContentLoader, compiler_reports: dict
) -> None:
    """The compiler's own strict run is clean, and the served form must be too."""
    for name, report in compiler_reports.items():
        assert report.unresolved_ops == [], name
        assert loader.unresolved_ops(name) == [], name


def test_the_check_can_fail(monkeypatch, loader: ContentLoader, compiler_reports: dict) -> None:
    """A check that cannot fail in the direction you care about is not a check.

    Silence the inliner and the accounting must part company with the compiler's. Without this,
    a green fidelity test would only prove that both sides ran.
    """
    monkeypatch.setattr(loader_mod, "inline_components", lambda text, components: (text, [], []))
    diverged = [
        name
        for name, report in compiler_reports.items()
        if loader.components_used(name) != report.components
    ]
    assert diverged, "the fidelity comparison did not notice a silent inliner"
