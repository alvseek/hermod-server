"""The loader serves the trimmed overlay and composes it the way the compiler does.

The counts here are facts about `agent-memory-project` at the pin this repo carries, not
constants someone liked: seven of its nine procedures, two templates, and every `§ op` a served
prompt references defined by the backend document it composes onto.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermod.content.loader import EXCLUDED_PROCEDURES, ContentLoader, ContentSource

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "agent-memory-project"

SERVED = [
    "awaken-coder",
    "load-project-context",
    "map-orientation",
    "project-wrap-up",
    "pull-project",
    "push-project",
    "update-project-context",
]


@pytest.fixture
def loader() -> ContentLoader:
    return ContentLoader([ContentSource(name="project", root=CONTENT)])


def test_serves_exactly_the_trimmed_set(loader: ContentLoader) -> None:
    assert loader.list_prompts() == SERVED


def test_the_exclusions_are_absent_and_unreachable(loader: ContentLoader) -> None:
    for name in EXCLUDED_PROCEDURES:
        assert name not in loader.list_prompts()
        with pytest.raises(KeyError):
            loader.get_prompt(name)


def test_two_templates_are_served(loader: ContentLoader) -> None:
    assert loader.list_resources() == ["orientation-map-template", "project-context-template"]


def test_an_unknown_prompt_raises(loader: ContentLoader) -> None:
    with pytest.raises(KeyError) as excinfo:
        loader.get_prompt("no-such-procedure")
    assert "unknown prompt" in str(excinfo.value)


def test_components_are_inlined_into_the_served_prompt(loader: ContentLoader) -> None:
    raw = (CONTENT / "procedures" / "awaken-coder.md").read_text(encoding="utf-8")
    served = loader.get_prompt("awaken-coder")
    assert "/components/" not in served, "a component link survived composition"
    assert len(served) > len(raw), "no component body was inserted"


def test_templates_are_not_inlined(loader: ContentLoader) -> None:
    """The overlay copies templates by path, so a served prompt has to keep the path."""
    assert "templates/orientation-map-template.md" in loader.get_prompt("map-orientation")


def test_arguments_are_substituted_only_when_given(loader: ContentLoader) -> None:
    """A no-argument served Prompt stays identical to the command it mirrors."""
    plain = loader.get_prompt("map-orientation")
    assert "$ARGUMENTS" in plain
    filled = loader.get_prompt("map-orientation", "create")
    assert "$ARGUMENTS" not in filled
    assert "create" in filled


def test_titles_and_descriptions_are_derived_and_distinct(loader: ContentLoader) -> None:
    prompts = loader.list_prompts()
    titles = {loader.title_prompt(name) for name in prompts}
    descriptions = {loader.describe_prompt(name) for name in prompts}
    assert len(titles) == len(prompts), "two procedures share a title"
    assert len(descriptions) == len(prompts), "two procedures share a description"
    assert loader.title_prompt("awaken-coder").startswith("Awaken Coder")


def test_every_op_a_served_prompt_references_resolves(loader: ContentLoader) -> None:
    for name in loader.list_prompts():
        assert loader.unresolved_ops(name) == [], name


def test_a_resource_is_served_verbatim(loader: ContentLoader) -> None:
    served = loader.get_resource("project-context-template")
    on_disk = (CONTENT / "templates" / "project-context-template.md").read_text(encoding="utf-8")
    assert served == on_disk


def test_an_absent_checkout_serves_nothing(tmp_path: Path) -> None:
    """A server without content still boots; it simply has none."""
    empty = ContentLoader([ContentSource(name="gone", root=tmp_path / "nope")])
    assert empty.available() is False
    assert empty.list_prompts() == []
    assert empty.list_resources() == []
