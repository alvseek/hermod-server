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
from hermod.business_services.context_service import ContextService
from hermod.content.loader import ContentLoader, ContentSource
from hermod.data_repositories.sqlite_project_repository import SqliteProjectRepository

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
def mcp(tmp_path) -> FastMCP:
    content = ContentLoader([ContentSource(name="project", root=CONTENT)])
    service = ContextService(
        SqliteProjectRepository(tmp_path / "hermod.db"),
        {name: content.template_version(name) for name in content.list_resources()},
    )
    return build_mcp(content, service)


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


async def test_a_prompt_argument_fills_the_placeholder(mcp: FastMCP) -> None:
    """The slot exists to save a round trip, and it must actually reach the text."""
    async with Client(mcp) as client:
        result = await client.get_prompt("map-orientation", {"mode": "create"})
    text = result.messages[0].content.text
    assert "$ARGUMENTS" not in text
    assert "create" in text


STORE_TOOLS = {
    "list_context_entries",
    "read_context_entry",
    "create_context_entry",
    "update_context_entry",
    "move_context_entry",
    "delete_context_entry",
    "check_entry_size",
    "map_exists",
    "read_map",
    "create_map",
    "write_map",
}


async def test_the_store_tools_share_the_face(mcp: FastMCP) -> None:
    """Phase 4's ops are reachable through the same door as the content, which is what makes a
    served db mechanic ("call § create-context-entry") resolvable by the agent reading it."""
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert STORE_TOOLS <= names
