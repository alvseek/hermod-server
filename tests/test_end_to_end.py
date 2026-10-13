"""The served face over real HTTP, driven by a real MCP client.

The in-memory client in ``test_mcp_surface.py`` proves the registration; this proves the
*transport*. A live uvicorn on a loopback port is what a coding agent actually connects to, and
the failure this catches is the one a description cannot: a session manager that never started,
an SSE frame that mangles a line, a mount that answers the wrong path.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastmcp import Client

from hermod.app import MCP_MOUNT_PATH, build_app
from hermod.configuration.config import Config
from hermod.content.loader import ContentLoader, ContentSource

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "agent-memory-project"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_server(tmp_path) -> Iterator[str]:
    """A real uvicorn on a loopback port, yielding the MCP URL a client would be given.

    It skips without a database, because the store is PostgreSQL and nothing else (decision 9).
    It still drives the store *being replaced*, so it does not move to the record repository here:
    that belongs with the wiring, and converting it now would be thrown away at the deletions in
    Step 1.2.
    """
    import os

    url = os.getenv("HERMOD_PG_TEST_URL")
    if not url:
        pytest.skip("HERMOD_PG_TEST_URL not set")
    port = _free_port()
    config = uvicorn.Config(
        build_app(Config(port=port, db_url=url)),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        threading.Event().wait(0.05)
    assert server.started, "the server never came up"
    yield f"http://127.0.0.1:{port}{MCP_MOUNT_PATH}"
    server.should_exit = True
    thread.join(timeout=10)


async def test_a_real_client_answers_over_http(live_server: str) -> None:
    async with Client(live_server) as client:
        pong = await client.call_tool("ping", {})
        prompts = await client.list_prompts()
    assert pong.data == "pong"
    assert len(prompts) == 7


async def test_a_real_client_reads_a_composed_procedure(live_server: str) -> None:
    async with Client(live_server) as client:
        result = await client.call_tool("read_procedure", {"name": "awaken-coder"})
    assert result.data["served"] is True
    assert result.data["content"].startswith("# Awaken Coder")
    assert "/components/" not in result.data["content"]


async def test_the_transport_does_not_alter_the_prompt(live_server: str) -> None:
    """Parity between what the loader composes and what arrives over the wire.

    A served procedure and the instruction an agent follows have to be the same text; the
    transport is the only layer between them that nobody looks at.
    """
    loader = ContentLoader([ContentSource(name="overlay", root=CONTENT)])
    async with Client(live_server) as client:
        for name in loader.list_prompts():
            result = await client.call_tool("read_procedure", {"name": name})
            assert result.data["content"] == loader.get_prompt(name), name


async def test_health_answers_on_the_live_server(live_server: str) -> None:
    """The smoke probe: the other face answers on the same process."""
    import httpx

    base = live_server.removesuffix(MCP_MOUNT_PATH)
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base}/health")
    assert response.status_code == 200
    assert response.json()["service"] == "hermod"


async def test_a_context_entry_round_trips_between_the_two_procedures(live_server: str) -> None:
    """Step 4.4: write through one procedure's op, read back through the other's.

    The two procedures call different tools — one creates an entry, the other lists and reads — so a
    round trip across both proves the store is reachable from the served face over real HTTP, and
    that the scope written is the scope read.
    """
    loader = ContentLoader([ContentSource(name="overlay", root=CONTENT)])
    stamp = loader.template_version("project-context-template")
    assert stamp, "the served context-entry template carries no stamp"
    async with Client(live_server) as client:
        created = (
            await client.call_tool(
                "create_context_entry",
                {
                    "project": "agent-memory-project",
                    "scope": "private",
                    "theme": "hermod-server-build",
                    "content": "# Hermod Server Build\n\nWhat the build left behind.\n",
                    "template_version": stamp,
                    "description": "the hermod-server build",
                    "tags": ["hermod-server"],
                },
            )
        ).data
        assert created["scope"] == "private"

        listed = (
            await client.call_tool("list_context_entries", {"project": "agent-memory-project"})
        ).data
        assert [(row["scope"], row["theme"]) for row in listed] == [
            ("private", "hermod-server-build")
        ]

        read = (
            await client.call_tool(
                "read_context_entry",
                {
                    "project": "agent-memory-project",
                    "scope": "private",
                    "theme": "hermod-server-build",
                },
            )
        ).data
        assert read["found"] is True
        assert read["scope"] == "private"
        assert read["content"].startswith("# Hermod Server Build")

        # The other scope is untouched: the entry is addressed, not searched for.
        assert (
            await client.call_tool(
                "read_context_entry",
                {
                    "project": "agent-memory-project",
                    "scope": "shared",
                    "theme": "hermod-server-build",
                },
            )
        ).data["found"] is False
