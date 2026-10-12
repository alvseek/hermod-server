"""Shared test infrastructure for the guarded face.

The MCP face is guarded **at the transport**, so a test that means to observe the guard has to
drive the *mounted* app over HTTP rather than an in-memory ``FastMCP``: an in-memory client sails
straight past the transport and would report success against a server that is wide open.

``auth_for`` supplies a caller — a real verifier holding real claims, so a test authenticates
rather than skips authenticating. What it substitutes is *which* issuer is trusted, never whether a
token is checked, and there is no helper here that turns verification off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

TEST_ISSUER = "https://hermod-tests.logto.invalid"


def token_for(subject: str) -> str:
    """The bearer string a test presents to act as ``subject``."""
    return f"test-token-{subject}"


def auth_for(*subjects: str) -> MultiAuth:
    """A verifier accepting exactly these subjects, each with its own token.

    The claims carry ``iss`` and ``sub`` — the two values a real Logto token supplies, arriving by
    the same route — so the verified-token path stays the shipped path on the one change where the
    difference is a security boundary.
    """
    return MultiAuth(
        verifiers=[
            StaticTokenVerifier(
                {
                    token_for(subject): {
                        "client_id": "hermod-tests",
                        "iss": TEST_ISSUER,
                        "sub": subject,
                    }
                    for subject in subjects
                }
            )
        ]
    )


@asynccontextmanager
async def running(app: Any) -> AsyncIterator[None]:
    """Start an app's lifespan.

    ``ASGITransport`` delivers requests but never fires lifespan events, so without this the mounted
    MCP app's session manager is never initialised and every session-opening call fails with a
    task-group error that looks nothing like an auth problem. Requests rejected at the transport do
    not need it, because they never reach the session manager.
    """
    async with app.router.lifespan_context(app):
        yield
