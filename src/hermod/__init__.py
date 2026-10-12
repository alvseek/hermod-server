"""Hermod — the coding overlay's MCP server.

Serves the `agent-memory-project` overlay (the coding shell, project memory and push/pull)
over MCP, so a client that cannot install markdown can still use it. The memory half of the
framework is Munnin's; a coding client connects both.

Local by default: with no issuer configured the server binds loopback and asks nobody to log in,
because it reads the repository it serves and therefore runs on the machine that holds it. Hosted,
it verifies Logto tokens in front of the MCP face, because that face exposes the project store
read and write and a server anyone can reach must not be one anyone can rewrite.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
