"""Hermod — the coding overlay's MCP server.

Serves the `agent-memory-project` overlay (the coding shell, project memory and push/pull)
over MCP, so a client that cannot install markdown can still use it. The memory half of the
framework is Munnin's; a coding client connects both.

Local only: there is no issuer, no token path and no secret, because Hermod reads the
repository it serves and therefore runs on the machine that holds it.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
