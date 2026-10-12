"""Hermod configuration.

**Local by default, verified when hosted.** Hermod reads a repository on the machine it runs on,
so its shipped shape binds loopback and asks nobody to log in. That default is safe but it is not
sufficient the moment the server is reachable beyond this machine: the MCP face exposes the project
store **read and write**, so a server anyone can reach would be one anyone can rewrite.

``logto_endpoint``, ``logto_audience`` and ``public_base_url`` configure the token verification that
stands in front of it when it is hosted. None of them is a secret: a resource server verifies
against a **public** JWKS and holds no client credential, which is why these sit in plain config
rather than behind the deploy's secret handling. The shape is Munnin's, ported, minus its tenancy —
Hermod's store is project-keyed rather than caller-keyed, so a verified token is a gate here and
never a tenant.

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
    # Hermod's own store: project-keyed memory (context entries, the orientation map).
    # Gitignored runtime data, one writer.
    db_path: Path = Path("data/hermod.db")
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
    # Whether the MCP face carries a verified token is decided by whether an issuer is configured,
    # and by nothing else — there is no mode switch here, deliberately. Munnin's switch works
    # because its default is ``token``; a switch on this server would be a field that can be set
    # wrong, and its two plausible defaults fail differently (one refuses the documented local run,
    # the other can silently leave a configured issuer unused). With the issuer itself as the switch
    # there is no third state to get wrong: tokens are verified exactly when there is something to
    # verify against, and the loopback guards below refuse to run open on a server anything else can
    # reach.
    #
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
        db_path=Path(os.getenv("HERMOD_DB_PATH", "data/hermod.db")),
        logto_endpoint=os.getenv("HERMOD_LOGTO_ENDPOINT", ""),
        logto_audience=tuple(
            entry.strip()
            for entry in os.getenv("HERMOD_LOGTO_AUDIENCE", "").split(",")
            if entry.strip()
        ),
        local_bind_all=os.getenv("HERMOD_LOCAL_BIND_ALL", "").lower() in {"1", "true", "yes"},
        public_base_url=os.getenv("HERMOD_PUBLIC_BASE_URL", "http://127.0.0.1:8210"),
    )
