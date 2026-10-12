"""Run Hermod: one uvicorn process serving the MCP face and ``/health``."""

from __future__ import annotations

import uvicorn

from hermod.app import build_app
from hermod.configuration.config import load_config


def main(argv: list[str] | None = None) -> int:
    """Start the server on the configured host and port."""
    config = load_config()
    uvicorn.run(build_app(config), host=config.host, port=config.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
