"""Composition root — build the ASGI app and wire the graph.

One process serves both surfaces:

- FastMCP's streamable-HTTP face, mounted at ``/mcp``
- ``/health``, which a deploy gate or a human reads

The mounted MCP app carries a lifespan — its session manager — that **must** be handed to the
parent app, or the MCP session manager never starts and the face answers nothing. That wiring is
the reason this file exists at all.

There is no HTTP API face and no auth middleware: this server serves content and liveness, and
nothing it serves needs a credential. Adding either later is a new router rather than a flag,
which is the point of not carrying the surface in the first place.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from hermod import __version__
from hermod.api_mcp.server import build_mcp
from hermod.configuration.config import Config, load_config
from hermod.content.loader import ContentLoader, ContentSource

#: Where the MCP face is mounted, and therefore the address a client connects to.
MCP_MOUNT_PATH = "/mcp"


async def health(request: Request) -> JSONResponse:
    """Liveness: the service name and its version, and nothing read from anywhere."""
    return JSONResponse({"status": "ok", "service": "hermod", "version": __version__})


def build_app(config: Config | None = None) -> Starlette:
    """Wire the graph: one loader, one MCP face, one app carrying both."""
    config = config or load_config()
    content = ContentLoader(
        [ContentSource(name="overlay", root=config.content_root)],
        backend_doc=config.backend_doc,
    )
    mcp_app = build_mcp(content).http_app(path="/")

    return Starlette(
        routes=[
            Route("/health", health),
            Mount(MCP_MOUNT_PATH, app=mcp_app),
        ],
        # Without this the MCP session manager never starts: the sub-app's lifespan is its own.
        lifespan=mcp_app.lifespan,
    )
