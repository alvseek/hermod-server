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
from tests.conftest import TEST_ISSUER, auth_for, token_for
from tests.valaskjalf_schema import make_store

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "agent-memory-project"

#: A caller for the round trip. The tenant comes from a token, so a test that writes to the store
#: has to present one; `auth_for` is the doubled verifier that accepts exactly this string.
SUBJECT = "e2e"
TOKEN = token_for(SUBJECT)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_server() -> Iterator[str]:
    """A real uvicorn on a loopback port, yielding the MCP URL a client would be given.

    Guarded by a doubled verifier and pointed at a real store, because the two facts the round
    trip depends on are structural: the tenant comes from a token, and the rows are records in
    Valaskjalf's tables. It skips without a database, since the store is PostgreSQL and nothing
    else (decision 9).
    """
    import os

    url = os.getenv("HERMOD_PG_TEST_URL")
    if not url:
        pytest.skip("HERMOD_PG_TEST_URL not set")
    # The mapping is seeded so the resolver lands on the tenant whose agent rows exist: a private
    # write needs an agent for its foreign key, and a tenant invented at first contact would have
    # none.
    make_store(url, user_id="alvi", agents=("meta",), identity=(TEST_ISSUER, SUBJECT))
    port = _free_port()
    config = uvicorn.Config(
        build_app(Config(port=port, db_url=url, content_roots=(CONTENT,)), auth=auth_for(SUBJECT)),
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
    async with Client(live_server, auth=TOKEN) as client:
        pong = await client.call_tool("ping", {})
        prompts = await client.list_prompts()
    assert pong.data == "pong"
    # Compared against the loader rather than against a number. The served count was written as a
    # literal in three test files and drifted in all three when a procedure was added, so the
    # claim here is the one that actually matters: the transport serves what the loader composes.
    served = ContentLoader([ContentSource(name="overlay", root=CONTENT)]).list_prompts()
    assert sorted(prompt.name for prompt in prompts) == sorted(served)


async def test_a_real_client_reads_a_composed_procedure(live_server: str) -> None:
    async with Client(live_server, auth=TOKEN) as client:
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
    async with Client(live_server, auth=TOKEN) as client:
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


async def test_a_context_entry_is_created_and_discoverable(live_server: str) -> None:
    """Write through the capture op, find it through the load op's listing.

    The two procedures call different tools — one creates an entry, the other lists a layer — so a
    round trip across both proves the store is reachable from the served face over real HTTP, and
    that the layer written is the layer listed. What the entry *body* is read with is the memory
    layer's `get(uuid)`, which is not connected here, so what this test can prove is that the
    create hands back the uuid which makes that read possible.
    """
    loader = ContentLoader([ContentSource(name="overlay", root=CONTENT)])
    stamp = loader.template_version("project-context-template")
    assert stamp, "the served context-entry template carries no stamp"
    async with Client(live_server, auth=TOKEN) as client:
        created = (
            await client.call_tool(
                "create_agent_context_entry",
                {
                    "project": "agent-memory-project",
                    "agent": "meta",
                    "theme": "hermod-server-build",
                    "content": "# Hermod Server Build\n\nWhat the build left behind.\n",
                    "template_version": stamp,
                    "content_summary": "the hermod-server build",
                    "tags": ["hermod-server"],
                },
            )
        ).data
        assert created["scope"] == "agent"
        assert created["content_summary"] == "the hermod-server build"
        assert created["uuid"], "the create must return the uuid the entry is addressed by"

        listed = (
            await client.call_tool(
                "list_agent_context", {"project": "agent-memory-project", "agent": "meta"}
            )
        ).data
        assert [(row["scope"], row["theme"], row["uuid"]) for row in listed] == [
            ("agent", "hermod-server-build", created["uuid"])
        ]

        # The other layer is untouched: the entry lands where the call said, not in both.
        assert (
            await client.call_tool(
                "list_shared_context", {"project": "agent-memory-project"}
            )
        ).data == []
