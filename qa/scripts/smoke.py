"""Read both content roots via the published loopback service, without store access."""

import asyncio
import json
import sys
from urllib.request import urlopen

from fastmcp import Client

BASE = "http://127.0.0.1:8210"


async def observe() -> None:
    with urlopen(f"{BASE}/health", timeout=5) as response:
        health = json.load(response)
    if health.get("service") != "hermod" or health.get("status") != "ok":
        raise RuntimeError(f"unexpected service at {BASE}: {health}")
    print("PASS Hermod health")

    async with Client(f"{BASE}/mcp", timeout=10) as client:
        for name, title, expansion in (
            ("awaken-coder", "# Awaken Coder", "Call `awaken_coder(domain, projects)`"),
            ("high-wizard", "# High Wizard", "## What Earns a Place"),
        ):
            result = await client.call_tool("read_procedure", {"name": name})
            data = result.data
            if not isinstance(data, dict) or data.get("served") is not True:
                raise RuntimeError(f"{name}: not served")
            text = data.get("content", "")
            if not text.startswith(title) or expansion not in text:
                raise RuntimeError(f"{name}: expected title or expanded instruction missing")
            print(f"PASS MCP read_procedure({name})")


if __name__ == "__main__":
    try:
        asyncio.run(observe())
    except Exception as exc:
        sys.exit(f"Hermod smoke failed: {exc}")
