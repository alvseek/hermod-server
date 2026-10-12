"""Hermod configuration.

**Local only, and that is structural rather than configured.** Hermod serves a repository that
lives on this machine, so there is nothing to reach from elsewhere and nothing to authenticate:
this file carries no issuer, no audience, no base URL and no mode switch, and the ASGI app
carries no auth middleware. Hosting Hermod would mean porting Munnin's `build_auth` and its
loopback guards, not flipping a flag here.

Two settings deserve a word because they are the seams the plan put here rather than in code:

``backend_doc``
    the storage backend a served prompt composes onto. The overlay ships two, and the served
    form has to pick one explicitly: a served client has no local store, so it composes onto
    the store's ops rather than the markdown ones. It defaults to the markdown document while
    the project store's mechanics are unwritten, and flips to ``db.md`` when they exist.

``content_root``
    the overlay checkout to serve, read live on every request. The default is the submodule
    path, so a clone with ``--recurse-submodules`` works with no configuration; the env var
    exists so development can point it at a working tree under edit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BACKEND_DOC = "storage-backends/markdown.md"


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


def load_config() -> Config:
    """Build config from defaults + optional env overrides (HERMOD_*)."""
    return Config(
        host=os.getenv("HERMOD_HOST", "127.0.0.1"),
        port=int(os.getenv("HERMOD_PORT", "8210")),
        content_root=Path(os.getenv("HERMOD_CONTENT_ROOT", "agent-memory-project")),
        backend_doc=os.getenv("HERMOD_BACKEND_DOC", DEFAULT_BACKEND_DOC),
        db_path=Path(os.getenv("HERMOD_DB_PATH", "data/hermod.db")),
    )
