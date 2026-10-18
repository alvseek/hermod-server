"""The served surface, driven through a real MCP client.

An in-memory client rather than a description of one, because the registration path has sharp
edges that asserting constants would not touch: a prompt's arguments are derived from a function
signature (both ``__signature__`` and ``__annotations__`` are load-bearing), and a resource has to
register as *static* rather than as a URI template.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from hermod.api_mcp.server import INSTRUCTIONS, build_mcp
from hermod.business_services.awakening_service import AwakeningService
from hermod.business_services.context_service import ContextService
from hermod.content.loader import ContentLoader, ContentSource
from hermod.data_repositories.record_project_repository import RecordProjectRepository

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
def mcp(tmp_path) -> FastMCP:
    content = ContentLoader([ContentSource(name="project", root=CONTENT)])
    service = ContextService(
        # Never connected: this file drives the registration, and no tool it calls reaches the
        # store. The literal is what the config would hold, not a server anywhere.
        lambda: RecordProjectRepository(user_id="unused", db_url="postgresql://unused"),
        {name: content.template_version(name) for name in content.list_resources()},
    )
    awakening = AwakeningService(
        lambda: RecordProjectRepository(user_id="unused", db_url="postgresql://unused")
    )
    return build_mcp(content, service, awakening)


async def test_a_client_lists_exactly_the_served_prompts(mcp: FastMCP) -> None:
    async with Client(mcp) as client:
        prompts = await client.list_prompts()
    assert sorted(p.name for p in prompts) == SERVED


async def test_the_menu_is_readable(mcp: FastMCP) -> None:
    """Derived titles and descriptions have to tell the procedures apart, or the menu is a list."""
    async with Client(mcp) as client:
        prompts = await client.list_prompts()
    assert len({p.title for p in prompts}) == len(prompts), "two prompts share a title"
    assert len({p.description for p in prompts}) == len(prompts), "two share a description"


async def test_the_exclusions_never_reach_the_handshake(mcp: FastMCP) -> None:
    async with Client(mcp) as client:
        names = {p.name for p in await client.list_prompts()}
    assert "push-all" not in names
    assert "pull-all" not in names


async def test_a_client_lists_the_served_resources(mcp: FastMCP) -> None:
    async with Client(mcp) as client:
        resources = await client.list_resources()
    assert sorted(r.name for r in resources) == [
        "orientation-map-template",
        "project-context-template",
    ]


async def test_read_procedure_round_trips(mcp: FastMCP) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("read_procedure", {"name": "awaken-coder"})
    assert result.data["served"] is True
    assert result.data["content"].startswith("# Awaken Coder")
    assert "/components/" not in result.data["content"]


async def test_an_unserved_name_is_an_answer_not_an_error(mcp: FastMCP) -> None:
    """A tool's caller is an agent typing what a procedure told it to type."""
    async with Client(mcp) as client:
        result = await client.call_tool("read_procedure", {"name": "generate-readme"})
    assert result.data["served"] is False
    assert "list_procedures()" in result.data["note"]


async def test_read_resource_round_trips(mcp: FastMCP) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("read_resource", {"name": "project-context-template"})
    assert result.data["served"] is True
    on_disk = (CONTENT / "templates" / "project-context-template.md").read_text(encoding="utf-8")
    assert result.data["content"] == on_disk, "a resource must be served verbatim"


async def test_ping_and_help(mcp: FastMCP) -> None:
    async with Client(mcp) as client:
        pong = await client.call_tool("ping", {})
        help_payload = (await client.call_tool("help", {})).data
    assert pong.data == "pong"
    assert help_payload["instructions"] == INSTRUCTIONS
    assert len(help_payload["procedures"]) == len(SERVED)


def test_the_instructions_state_the_template_rule() -> None:
    """A served procedure names a template by path, and a client has no such checkout.

    The rule belongs in the instructions because it is the one thing a client cannot derive: the
    route and the resource share a flat name, so a path's stem is its URL.
    """
    assert "downloaded, not retyped" in INSTRUCTIONS
    assert "/content/<stem>" in INSTRUCTIONS


async def test_a_prompt_argument_fills_the_placeholder(mcp: FastMCP) -> None:
    """The slot exists to save a round trip, and it must actually reach the text."""
    async with Client(mcp) as client:
        result = await client.get_prompt("map-orientation", {"mode": "create"})
    text = result.messages[0].content.text
    assert "$ARGUMENTS" not in text
    assert "create" in text


STORE_TOOLS = {
    "list_shared_context",
    "list_agent_context",
    "create_shared_context_entry",
    "create_agent_context_entry",
    "promote_context_entry",
    "demote_context_entry",
    "read_map",
    "create_map",
    "update_map",
}

#: The names this store used to serve and no longer does. Reading a body, writing one, writing
#: metadata and retiring a row are the memory layer's, and the file-era checks were each answered
#: by an op that already existed.
DROPPED_TOOLS = {
    "list_context_entries",
    "read_context_entry",
    "create_context_entry",
    "update_context_entry",
    "move_context_entry",
    "delete_context_entry",
    "check_entry_size",
    "map_exists",
    "write_map",
}


async def test_the_store_tools_share_the_face(mcp: FastMCP) -> None:
    """Discovery, creation and the map are reachable through the same door as the content, which
    is what makes a served db mechanic resolvable by the agent reading it.

    The dropped names are asserted absent in the same pass: a face that keeps a tool nobody
    documents is worse than one that dropped it where it can be seen.
    """
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert STORE_TOOLS <= names
    assert not (DROPPED_TOOLS & names)


async def test_the_awakening_tool_is_registered_and_refuses_an_empty_set(mcp: FastMCP) -> None:
    """The face's one *assembled* payload, named here so it cannot be dropped silently.

    Called with no projects it refuses before reading anything, which is what lets this test prove
    the tool is reachable without a database behind it: the guard is the service's first line, so
    nothing here reaches the store.
    """
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert "awaken_coder" in names
        with pytest.raises(Exception, match="at least one project"):
            await client.call_tool("awaken_coder", {"domain": "meta", "projects": []})
        # A blank element is refused too: `projects[0]` is the session's repository, so filtering
        # one out would answer about a different repository than the caller named.
        with pytest.raises(Exception, match="must name one"):
            await client.call_tool("awaken_coder", {"domain": "meta", "projects": [""]})
