"""The MCP face behind a token, and the guard that keeps it open only on this machine.

These drive the **mounted** app over HTTP rather than an in-memory ``FastMCP``, because that is the
only place the guard exists: authentication is enforced at the transport, so an in-memory client
would sail past it and report success on a server that is wide open. A test that cannot observe the
guard cannot prove the guard.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from httpx import ASGITransport
from starlette.testclient import TestClient

from hermod.app import build_app, build_auth
from hermod.configuration.config import Config
from tests.conftest import auth_for, running, token_for

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "tests", "version": "0"},
    },
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


# The store is PostgreSQL and nothing else (decision 9), and `build_app` refuses a config with no
# DSN. These tests never call a store tool, so the value only has to be present: the repository
# does not connect until an op runs.
_UNUSED_STORE = "postgresql://unused:unused@localhost:5432/unused"

# A configured-but-absent content source is refused at boot, so every app test names a real one.
_CONTENT = Path(__file__).resolve().parents[1] / "agent-memory-project"


def _guarded(_db: Path, *subjects: str):
    return build_app(
        Config(db_url=_UNUSED_STORE, content_roots=(_CONTENT,)), auth=auth_for(*subjects)
    )


def _local(_db: Path):
    """The unguarded shape, kept for the negative control below: no issuer, so no guard to prove."""
    config = Config(db_url=_UNUSED_STORE, content_roots=(_CONTENT,))
    assert build_auth(config) is None, "the defaults must stay local, or this control is invalid"
    return build_app(config)


def _raw(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _bearer(subject: str) -> dict[str, str]:
    return {**MCP_HEADERS, "Authorization": f"Bearer {token_for(subject)}"}


async def test_a_call_with_no_token_is_refused(tmp_path: Path) -> None:
    async with _raw(_guarded(tmp_path / "h.db", "subj-a")) as client:
        resp = await client.post("/mcp/", json=INIT, headers=MCP_HEADERS)
    assert resp.status_code == 401


async def test_a_token_the_issuer_never_minted_is_refused(tmp_path: Path) -> None:
    headers = {**MCP_HEADERS, "Authorization": "Bearer not-a-real-token"}
    async with _raw(_guarded(tmp_path / "h.db", "subj-a")) as client:
        resp = await client.post("/mcp/", json=INIT, headers=headers)
    assert resp.status_code == 401


async def test_no_tool_is_reachable_unauthenticated(tmp_path: Path) -> None:
    """``ping`` reads nothing and is the likeliest thing to be waved through."""
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ping"}}
    async with _raw(_guarded(tmp_path / "h.db", "subj-a")) as client:
        resp = await client.post("/mcp/", json=body, headers=MCP_HEADERS)
    assert resp.status_code == 401


async def test_health_stays_open_without_a_token(tmp_path: Path) -> None:
    """The deploy's health gate calls this unauthenticated, so guarding it fails the cutover."""
    async with _raw(_guarded(tmp_path / "h.db", "subj-a")) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "hermod"


async def test_a_verified_token_opens_the_face(tmp_path: Path) -> None:
    app = _guarded(tmp_path / "h.db", "subj-a")
    async with running(app), _raw(app) as client:
        resp = await client.post("/mcp/", json=INIT, headers=_bearer("subj-a"))
    assert resp.status_code == 200
    assert "protocolVersion" in resp.text


async def test_a_token_for_a_different_subject_still_opens_the_face(tmp_path: Path) -> None:
    """Hermod has no tenancy: the token is a gate, not a partition, so any verified caller is in.

    Recorded as a test because it is the property that separates this server's auth from Munnin's —
    if a tenant ever appears here, this is the test that should fail first.
    """
    app = _guarded(tmp_path / "h.db", "subj-a", "subj-b")
    async with running(app), _raw(app) as client:
        resp = await client.post("/mcp/", json=INIT, headers=_bearer("subj-b"))
    assert resp.status_code == 200


async def test_the_same_call_is_answered_when_the_guard_is_absent(tmp_path: Path) -> None:
    """Negative control — proves the 401s above come from the guard and not from the request.

    Every other assertion in this file is that something is *refused*, and a malformed request
    would be refused too. So one unguarded face is built and the identical call made against it:
    if this ever starts answering 401 as well, the tests above have stopped testing authentication
    and nothing else would say so.
    """
    app = _local(tmp_path / "h.db")
    async with running(app), _raw(app) as client:
        resp = await client.post("/mcp/", json=INIT, headers=MCP_HEADERS)
    assert resp.status_code == 200


def test_neither_slash_form_redirects(tmp_path: Path) -> None:
    """A client configured with the no-slash form must not be bounced through a proxy.

    Asserted with redirects **off**, because they are followed by default and a 307 to the slashed
    form would then look exactly like a 200. Behind a TLS-terminating proxy that redirect is built
    from the scheme uvicorn saw, so it points at ``http://`` and drops the client to plaintext
    before it ever sees a 401.
    """
    with TestClient(_guarded(tmp_path / "h.db", "subj-a"), follow_redirects=False) as client:
        for path in ("/mcp", "/mcp/"):
            response = client.post(path, json=INIT, headers=MCP_HEADERS)
            assert response.status_code not in {301, 302, 307, 308}, path
            # Reaching the guard at all is the point: a redirect would answer before it.
            assert response.status_code == 401, path


def test_the_metadata_document_is_served_from_the_root(tmp_path: Path) -> None:
    """RFC 9728 discovery has to answer where the 401 says it does, not under the mount.

    FastMCP builds its app believing it sits at ``/``, so its own copy of the metadata route lands
    under ``/mcp`` while the challenge advertises the root path. A client following the standard
    flow would fail discovery and never reach a login screen.

    Built from the real provider rather than the test double: ``build_auth`` is what registers the
    document, and the double stands in for the verifier, not for the discovery surface.
    """
    app = build_app(
        Config(
            db_url=_UNUSED_STORE,
            content_roots=(_CONTENT,),
            logto_endpoint="https://auth.lok.quest",
            public_base_url="https://hermod.lok.quest",
        )
    )
    with TestClient(app) as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    assert response.json()["resource"] == "https://hermod.lok.quest/mcp"
