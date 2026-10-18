"""The loader serves the trimmed overlay and composes it the way the compiler does.

The counts here are facts about `agent-memory-project` at the pin this repo carries, not
constants someone liked: eight of its ten procedures, two templates, and every `§ op` a served
prompt references defined by the backend document it composes onto.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermod.content.loader import EXCLUDED_PROCEDURES, ContentLoader, ContentSource

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "agent-memory-project"

SERVED = [
    "add-coding-reasoning",
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


def _write_resource(root: Path, dirname: str, stem: str, body: str) -> None:
    (root / dirname).mkdir(parents=True, exist_ok=True)
    (root / dirname / f"{stem}.md").write_text(body, encoding="utf-8")


def test_every_resource_file_is_served_once(loader: ContentLoader) -> None:
    """The served set is flat, so a stem carried twice shadows rather than adds.

    Counting files against names is the cheap form of that guard: one file per name means no
    collision, and a planted duplicate breaks the equality.
    """
    files = [
        path
        for source in loader.sources()
        for dirname in ("templates", "plan-templates")
        for path in (source.root / dirname).glob("*.md")
    ]
    assert len(loader.list_resources()) == len(files)


def test_a_source_with_no_resource_directories_contributes_nothing(tmp_path: Path) -> None:
    """``agent-memory-fleet`` carries no plan templates, and a missing directory must not raise."""
    (tmp_path / "procedures").mkdir()
    loader = ContentLoader([ContentSource(name="bare", root=tmp_path)])
    assert loader.list_resources() == []
    with pytest.raises(KeyError):
        loader.get_resource("anything")


def test_plan_templates_are_served_beside_templates(tmp_path: Path) -> None:
    """Both directories reach the one namespace, and the earlier source wins a shared name."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_resource(first, "templates", "shared", "first body\n")
    _write_resource(second, "plan-templates", "shared", "second body\n")
    _write_resource(first, "plan-templates", "planner", "planner body\n")

    loader = ContentLoader(
        [ContentSource(name="first", root=first), ContentSource(name="second", root=second)]
    )

    assert loader.list_resources() == ["planner", "shared"]
    assert loader.get_resource("planner") == "planner body\n"
    assert loader.get_resource("shared") == "first body\n"


def test_a_second_source_is_composed_from_its_own_components(tmp_path: Path) -> None:
    """Components resolve beside the procedure that references them, not from the first source.

    This is the defect the loopback proof found: with one root for the whole loader, eight of the
    wizards' prompts were served with no components inlined at all, and nothing said so.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "procedures").mkdir(parents=True)
    (first / "procedures" / "one.md").write_text("first body\n", encoding="utf-8")
    (second / "procedures").mkdir(parents=True)
    (second / "procedures" / "two.md").write_text(
        "Lead in. [The Thing](../../second/components/thing.md) tail.\n", encoding="utf-8"
    )
    (second / "components").mkdir()
    (second / "components" / "thing.md").write_text(
        "# Thing\n\n*Not a standalone skill.*\n\n---\n\nTHING BODY\n", encoding="utf-8"
    )

    loader = ContentLoader(
        [ContentSource(name="first", root=first), ContentSource(name="second", root=second)]
    )

    assert loader.list_prompts() == ["one", "two"]
    assert "THING BODY" in loader.get_prompt("two")
    assert loader.missing_components("two") == ()


def test_a_missing_component_is_reported_not_dropped(tmp_path: Path) -> None:
    """A reference that resolves nowhere must be askable, not silently absent from the prompt."""
    (tmp_path / "procedures").mkdir()
    (tmp_path / "procedures" / "p.md").write_text(
        "See [Gone](../../nowhere/components/gone.md).\n", encoding="utf-8"
    )
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "present.md").write_text(
        "# Present\n\n*Not a standalone skill.*\n\n---\n\nPRESENT\n", encoding="utf-8"
    )

    loader = ContentLoader([ContentSource(name="solo", root=tmp_path)])

    assert loader.missing_components("p") == ("gone",)
    assert "Gone" in loader.get_prompt("p"), "the label survives the de-linking"


def test_no_served_prompt_has_a_missing_component(loader: ContentLoader) -> None:
    """The overlay's own prompts are complete, which is the property every source must hold."""
    assert [name for name in loader.list_prompts() if loader.missing_components(name)] == []


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


def test_the_served_backend_is_the_store(loader: ContentLoader) -> None:
    """Step 4.3: the composed ops are the store's tool calls, not markdown's file mechanics.

    Both creates are asserted: the layer is in the tool's name now, so a parser that only looked
    for one name would let the other half of the surface go unserved.
    """
    served = loader.get_prompt("update-project-context")
    assert "create_shared_context_entry(project" in served
    assert "create_agent_context_entry(project" in served


def test_the_served_wrap_up_pushes_the_project_not_the_store(loader: ContentLoader) -> None:
    """``db.md``'s § persist-store resolves to /push-project: the store is the server's own file,
    written as every op runs, so the memory half of the persistence dissolves and only the
    project's commits are left to push. `/push-all` is unserved, so the served text must not name
    it as a command to run."""
    served = loader.get_prompt("project-wrap-up")
    assert "/push-project" in served
    assert "/push-all" not in served


def test_the_served_awaken_coder_delegates_the_episode_step(loader: ContentLoader) -> None:
    """Episodes are Munnin's records and the store holds none, so § select-project-episode
    resolves to a delegation rather than a store read."""
    served = loader.get_prompt("awaken-coder")
    assert "§ select-project-episode" in served
    assert "memory layer" in served


def test_a_resource_is_served_verbatim(loader: ContentLoader) -> None:
    served = loader.get_resource("project-context-template")
    on_disk = (CONTENT / "templates" / "project-context-template.md").read_text(encoding="utf-8")
    assert served == on_disk


def test_the_served_templates_carry_a_version_stamp(loader: ContentLoader) -> None:
    """Every served template must carry a stamp: it is what a store write has to declare."""
    for name in loader.list_resources():
        stamp = loader.template_version(name)
        assert stamp, f"template {name!r} carries no template_version stamp"


def test_no_served_resource_reads_as_metadata(loader: ContentLoader) -> None:
    """A resource's menu text must be the template's prose, never its bookkeeping.

    Both shapes are checked because the framework serves both: a template can lead with a
    ``template_version`` stamp or with a frontmatter block, and either one reaching a client's
    picker is the fault this pins.
    """
    for name in loader.list_resources():
        title = loader.title_resource(name)
        description = loader.describe_resource(name)
        assert title and not title.startswith("["), name
        assert description, name
        assert not description.startswith("---"), name
        assert not description.startswith("template_version"), name


def test_a_metadata_leading_template_still_reads_as_prose(tmp_path: Path) -> None:
    """The skip is by shape, so a template that is not in front of us today is covered too."""
    root = tmp_path / "checkout"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "stamped.md").write_text(
        "# Stamped\n\ntemplate_version: 2026-01-01-00-00\n\nWhat it is for.\n", encoding="utf-8"
    )
    (root / "templates" / "frontmatter.md").write_text(
        '---\nproject: "p"\n---\n\n# Front\n\nWhat it is for.\n', encoding="utf-8"
    )
    loader = ContentLoader([ContentSource(name="x", root=root)])
    assert loader.describe_resource("stamped") == "What it is for."
    assert loader.describe_resource("frontmatter") == "What it is for."


def test_an_unstamped_template_reads_as_none(tmp_path: Path) -> None:
    """``None`` is how a template without a stamp answers, and the store refuses on it."""
    root = tmp_path / "checkout"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "plain.md").write_text("# Plain\n\nNo stamp here.\n", encoding="utf-8")
    loader = ContentLoader([ContentSource(name="x", root=root)])
    assert loader.template_version("plain") is None


def test_an_absent_checkout_serves_nothing(tmp_path: Path) -> None:
    """A server without content still boots; it simply has none."""
    empty = ContentLoader([ContentSource(name="gone", root=tmp_path / "nope")])
    assert empty.available() is False
    assert empty.list_prompts() == []
    assert empty.list_resources() == []
