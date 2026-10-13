"""Hermod configuration.

**Two shapes, one switch: the issuer.** With an issuer configured, the MCP face verifies every
token, and the verified token names the tenant whose rows the call may touch. With none, the
server runs the **local** shape: loopback only and unverified, with the two guards below refusing
any configuration that would leave it reachable from elsewhere.

Local mode serves the **content** surface — the overlay's procedures and templates — and nothing
past it. It cannot serve the store, because the tenant comes from a token: every store op raises
rather than answering under a guessed account. That is why the DSN is required in both shapes, and
why a local run is a way to read the overlay rather than a way to use the store.

``logto_endpoint``, ``logto_audience`` and ``public_base_url`` configure the token verification that
stands in front of it when it is hosted. None of them is a secret: a resource server verifies
against a **public** JWKS and holds no client credential, which is why these sit in plain config
rather than behind the deploy's secret handling. The shape is Munnin's, ported: Hermod resolves a
tenant from the verified token exactly as Munnin does, because the rows it reads carry one.

Two settings deserve a word because they are the seams the plan put here rather than in code:

``backend_doc``
    the storage backend a served prompt composes onto. The overlay ships two, and the served
    form has to pick one explicitly: a served client has no local store, so it composes onto
    the store's ops rather than the markdown ones, which is why ``db.md`` is the default.

``content_root``
    the overlay checkout to serve, read live on every request. The default is the submodule
    path, so a clone with ``--recurse-submodules`` works with no configuration; the env var
    exists so development can point it at a working tree under edit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BACKEND_DOC = "storage-backends/db.md"


@dataclass(frozen=True)
class Config:
    """Runtime settings. Env vars override defaults where present."""

    host: str = "127.0.0.1"
    # 8210, not Munnin's 8200: a coding client connects both servers, and the two are meant to
    # run side by side on one machine.
    port: int = 8210
    # The overlay this server serves — the agent-memory-project submodule, read live.
    content_root: Path = Path("agent-memory-project")
    # The storage backend document served prompts compose onto (repo-relative to content_root).
    backend_doc: str = DEFAULT_BACKEND_DOC
    # The Valaskjalf DSN: the memory store's database, which holds project-keyed memory as
    # `knowledge` records carrying a `project` (ADR-027). One engine, because it is the only one
    # deployed; SQLite was carried here so the suite could run without a service and was removed
    # with decision 9. It carries the database password, so it is the one secret this server holds
    # and the only value the deploy keeps out of plain config.
    db_url: str = ""
    # Logto tenant endpoint, e.g. "https://auth.lok.quest" (Console -> Settings -> Domains). When
    # set, Logto mints the tokens the MCP face verifies. The OIDC paths beneath it are fixed by
    # Logto, so they are derived rather than configured.
    logto_endpoint: str = ""
    # Overrides the audience tokens are checked against. Normally left empty, because the audience
    # binds itself to the resource URL this server advertises. Set it when the registered API
    # Identifier differs from that URL, or when more than one is in play: a token request's
    # ``resource`` parameter has to match a registered identifier character for character, and a
    # client that authorizes against one identifier while refreshing against another mints tokens
    # under both. Comma-separated, so a rename never needs a window in which neither is accepted.
    logto_audience: tuple[str, ...] = ()
    # Local mode's second guard, reached only when no issuer is configured. A process bound to
    # ``0.0.0.0`` with no authentication is open to every network the machine is on — unless the
    # operator states, with this flag, that something in front of it (a container port published on
    # the host's loopback) keeps it local. The flag is an acknowledgement, never a detection: the
    # server cannot know what publishes its port, so it asks to be told.
    local_bind_all: bool = False
    # This server's own public URL. Tokens are bound to it as their audience, so it must match the
    # Resource Indicator registered with the issuer. Loopback by default — the only value local
    # mode accepts, and the right one for a laptop; a deployment sets its own.
    public_base_url: str = "http://127.0.0.1:8210"


def load_config() -> Config:
    """Build config from defaults + optional env overrides (HERMOD_*)."""
    return Config(
        host=os.getenv("HERMOD_HOST", "127.0.0.1"),
        port=int(os.getenv("HERMOD_PORT", "8210")),
        content_root=Path(os.getenv("HERMOD_CONTENT_ROOT", "agent-memory-project")),
        backend_doc=os.getenv("HERMOD_BACKEND_DOC", DEFAULT_BACKEND_DOC),
        db_url=os.getenv("HERMOD_DB_URL", ""),
        logto_endpoint=os.getenv("HERMOD_LOGTO_ENDPOINT", ""),
        logto_audience=tuple(
            entry.strip()
            for entry in os.getenv("HERMOD_LOGTO_AUDIENCE", "").split(",")
            if entry.strip()
        ),
        local_bind_all=os.getenv("HERMOD_LOCAL_BIND_ALL", "").lower() in {"1", "true", "yes"},
        public_base_url=os.getenv("HERMOD_PUBLIC_BASE_URL", "http://127.0.0.1:8210"),
    )
