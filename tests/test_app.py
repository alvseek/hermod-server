"""The app carries both surfaces, and the MCP lifespan is genuinely wired.

The lifespan is the sharp edge here: the mounted MCP app owns the session manager, so a parent
that forgets to take its lifespan boots happily and then answers nothing. That is why these tests
drive a handshake rather than asserting the app was constructed.

The guard's own behaviour lives in ``test_auth.py``; what is here is the composition — which
configurations build, which are refused, and that the refusal is loud rather than a quiet downgrade.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermod import __version__
from hermod.app import (
    MCP_MOUNT_PATH,
    ContentUnavailableError,
    LocalModeNotLoopbackError,
    build_app,
)
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


# The store is PostgreSQL and nothing else (decision 9), and `build_app` refuses a config with no
# DSN. None of these tests call a store tool, so the value only has to be present.
_UNUSED_STORE = "postgresql://unused:unused@localhost:5432/unused"


def _config(**overrides) -> Config:
    # Every app test needs a checkout, because a configured-but-absent source is refused at boot.
    overrides.setdefault("content_roots", (CONTENT,))
    return Config(db_url=_UNUSED_STORE, **overrides)


# The overlay checkout, addressed absolutely: the app's default is a relative path, which only
# resolves from the repository root, and a content test should not depend on the caller's cwd.
CONTENT = Path(__file__).resolve().parents[1] / "agent-memory-project"


def test_an_absent_content_source_is_refused_at_boot(tmp_path: Path) -> None:
    """A server with no prompts answers /health and looks healthy, so this has to fail loudly."""
    with pytest.raises(ContentUnavailableError):
        build_app(_config(content_roots=(tmp_path / "gone",)))


def test_a_template_downloads_byte_for_byte() -> None:
    """The route serves the file itself, so a client copies bytes rather than retyping text."""
    path = CONTENT / "templates" / "project-context-template.md"
    with TestClient(build_app(_config(content_roots=(CONTENT,)))) as client:
        response = client.get("/content/project-context-template")
    assert response.status_code == 200
    assert response.content == path.read_bytes()
    assert response.headers["content-type"].startswith("text/markdown")


def test_an_unknown_name_is_a_404_and_traversal_cannot_be_expressed() -> None:
    with TestClient(build_app(_config(content_roots=(CONTENT,)))) as client:
        assert client.get("/content/nope").status_code == 404
        # A slash cannot appear in the route's parameter, so this is an unknown name rather
        # than a path that escapes the served set.
        assert client.get("/content/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_the_content_route_sits_outside_the_token_guard() -> None:
    """An agent's own shell call carries no token, so a download cannot require one."""
    guarded = _config(
        content_roots=(CONTENT,),
        logto_endpoint="https://auth.lok.quest",
        public_base_url="https://hermod.lok.quest",
        host="0.0.0.0",
    )
    with TestClient(build_app(guarded)) as client:
        assert client.get("/content/project-context-template").status_code == 200
        assert client.post(MCP_MOUNT_PATH, json=INITIALIZE, headers=HEADERS).status_code == 401


def test_health_answers_with_the_service_identity() -> None:
    with TestClient(build_app(_config())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "hermod", "version": __version__}


def test_the_mcp_face_answers_a_handshake() -> None:
    """Proves the session manager started: without the lifespan this comes back empty."""
    with TestClient(build_app(_config())) as client:
        response = client.post(MCP_MOUNT_PATH, json=INITIALIZE, headers=HEADERS)
    assert response.status_code == 200
    assert "protocolVersion" in response.text
    assert "hermod" in response.text


def test_both_slash_forms_reach_the_face_without_a_redirect() -> None:
    """A client configured with the no-slash form — the one the spec prefers — must not bounce.

    Asserted with redirects off, because a 307 to the slashed form is followed by default and would
    then look identical to a 200. The normaliser is what makes this hold; without it the bare path
    answers from Starlette's Mount instead of reaching the sub-app.
    """
    with TestClient(build_app(_config()), follow_redirects=False) as client:
        for path in (MCP_MOUNT_PATH, MCP_MOUNT_PATH + "/"):
            response = client.post(path, json=INITIALIZE, headers=HEADERS)
            assert response.status_code == 200, path


def test_the_app_builds_local_with_no_issuer_on_loopback() -> None:
    """The laptop shape, unchanged: no issuer, loopback, nothing to authenticate against."""
    app = build_app(_config())
    assert app is not None


def test_a_public_url_with_no_issuer_is_refused() -> None:
    """The one combination that must never start: reachable from elsewhere, and open."""
    with pytest.raises(LocalModeNotLoopbackError, match="not this machine"):
        build_app(_config(public_base_url="https://hermod.lok.quest"))


def test_a_public_bind_with_no_issuer_is_refused() -> None:
    with pytest.raises(LocalModeNotLoopbackError, match="every interface"):
        build_app(_config(host="0.0.0.0"))


def test_a_public_bind_is_allowed_when_it_is_explicitly_acknowledged() -> None:
    """The container shape: bind 0.0.0.0 behind a port published on the host's loopback."""
    app = build_app(_config(host="0.0.0.0", local_bind_all=True))
    assert app is not None


def test_the_app_builds_with_an_issuer_configured() -> None:
    """Hosted: an issuer is set, so a verifier is built and discovery is served from the root."""
    app = build_app(
        _config(logto_endpoint="https://auth.lok.quest", public_base_url="https://hermod.lok.quest")
    )
    paths = {getattr(route, "path", None) for route in app.router.routes}
    assert "/.well-known/oauth-protected-resource/mcp" in paths
