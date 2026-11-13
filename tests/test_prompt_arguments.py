"""Argument support follows each procedure's placeholder, not a second name inventory."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from hermod.api_mcp.server import build_mcp
from hermod.business_services.awakening_service import AwakeningService
from hermod.business_services.context_service import ContextService
from hermod.content.loader import PROMPT_ARGUMENTS, ContentLoader, ContentSource

ROOT = Path(__file__).resolve().parents[1]
LOADER = ContentLoader(
    [
        ContentSource(name="project", root=ROOT / "agent-memory-project"),
        ContentSource(name="wizards", root=ROOT / "agent-memory-wizards"),
    ]
)
MARKER = "argument-probe: add postgres and preserve this text"


def _server(loader: ContentLoader):
    def no_repository():
        raise AssertionError("reading a procedure must not access the store")

    return build_mcp(loader, ContextService(no_repository), AwakeningService(no_repository))


@pytest.fixture(scope="module")
def prompt_server():
    return _server(LOADER)


@pytest.mark.parametrize("name", LOADER.list_prompts())
async def test_every_served_prompt_preserves_its_arguments(prompt_server, name: str) -> None:
    """Pin the input/output invariant independently of the loader's declaration registry."""
    authored = LOADER.get_prompt(name)
    async with Client(prompt_server) as client:
        prompts = {prompt.name: prompt for prompt in await client.list_prompts()}
        prompt = prompts[name]
        arguments = prompt.arguments or []
        plain = await client.get_prompt(name)
        assert plain.messages[0].content.text == authored
        if "$ARGUMENTS" in authored:
            assert len(arguments) == 1, f"{name} drops input: placeholder has no declared slot"
        else:
            assert not arguments, f"{name} advertises an argument its body cannot use"
            return

        argument = arguments[0]
        assert not argument.required
        assert argument.description
        filled = await client.get_prompt(name, {argument.name: MARKER})
        expected = authored.replace("$ARGUMENTS", MARKER)
        assert filled.messages[0].content.text == expected
        assert MARKER in expected
        assert "$ARGUMENTS" not in expected
        # The model-invoked tool and the user-invoked prompt must bind the same input.
        through_tool = await client.call_tool("read_procedure", {"name": name, "argument": MARKER})
        assert through_tool.data["content"] == expected


def test_existing_named_slots_keep_their_names() -> None:
    expected = {
        "awaken-coder": "domain",
        "load-project-context": "keyword",
        "map-orientation": "mode",
        "project-wrap-up": "mode",
        "push-project": "mode",
        "update-project-context": "context",
    }
    for name, argument in expected.items():
        assert LOADER.argument_prompt(name)[0] == argument


@pytest.mark.parametrize("source_index", [0, 1])
async def test_a_new_procedure_gets_argument_support_without_a_map_entry(
    tmp_path: Path, source_index: int
) -> None:
    sources = [
        ContentSource(name="first", root=tmp_path / "first"),
        ContentSource(name="second", root=tmp_path / "second"),
    ]
    for source in sources:
        (source.root / "procedures").mkdir(parents=True)
    path = sources[source_index].root / "procedures" / "new-unlisted-procedure.md"
    path.write_text(
        "# New Procedure\n\n## Arguments\n\n$ARGUMENTS\n", encoding="utf-8", newline="\n"
    )
    assert path.stem not in PROMPT_ARGUMENTS
    loader = ContentLoader(sources)
    async with Client(_server(loader)) as client:
        prompt = (await client.list_prompts())[0]
        assert len(prompt.arguments) == 1
        assert prompt.arguments[0].name == "argument"
        assert not prompt.arguments[0].required
        filled = await client.get_prompt(path.stem, {"argument": MARKER})
    assert filled.messages[0].content.text == path.read_text(encoding="utf-8").replace(
        "$ARGUMENTS", MARKER
    )


def test_a_new_parameterless_procedure_stays_parameterless(tmp_path: Path) -> None:
    directory = tmp_path / "procedures"
    directory.mkdir()
    (directory / "no-input.md").write_text(
        "# No Input\n\nFollow the approved plan.\n", encoding="utf-8", newline="\n"
    )
    loader = ContentLoader([ContentSource(name="only", root=tmp_path)])
    assert loader.argument_prompt("no-input") is None


def test_unknown_procedure_argument_lookup_is_refused() -> None:
    with pytest.raises(KeyError, match="unknown prompt"):
        LOADER.argument_prompt("no-such-procedure")
