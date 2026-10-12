"""The app carries both surfaces, and the MCP lifespan is genuinely wired.

The lifespan is the sharp edge here: the mounted MCP app owns the session manager, so a parent
that forgets to take its lifespan boots happily and then answers nothing. That is why these tests
drive a handshake rather than asserting the app was constructed.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from hermod import __version__
from hermod.app import MCP_MOUNT_PATH, build_app
from hermod.configuration.config import Config

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def test_health_answers_with_the_service_identity() -> None:
    with TestClient(build_app(Config())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "hermod", "version": __version__}


def test_the_mcp_face_answers_a_handshake() -> None:
    """Proves the session manager started: without the lifespan this comes back empty."""
    with TestClient(build_app(Config())) as client:
        response = client.post(MCP_MOUNT_PATH, json=INITIALIZE, headers=HEADERS)
    assert response.status_code == 200
    assert "protocolVersion" in response.text
    assert "hermod" in response.text


def test_both_slash_forms_reach_the_face_without_a_redirect() -> None:
    """A client configured with the no-slash form — the one the spec prefers — must not bounce.

    Measured rather than assumed: with this Starlette and this MCP app, neither form redirects,
    so no path normaliser is carried.
    """
    with TestClient(build_app(Config())) as client:
        for path in (MCP_MOUNT_PATH, MCP_MOUNT_PATH + "/"):
            response = client.post(path, json=INITIALIZE, headers=HEADERS)
            assert response.status_code == 200, path


def test_the_app_builds_with_no_issuer_configured() -> None:
    """Local only: there is no issuer to configure and nothing refuses to start without one."""
    app = build_app(Config())
    assert app is not None
