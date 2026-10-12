# Hermod — the coding overlay's MCP server

Hermod serves the [agent-memory-project](https://github.com/alvseek/agent-memory-project) overlay — the coding shell, project memory and push/pull — over MCP, so a client that cannot install markdown can still use it. It is the coding half of the [agent-memory framework](https://github.com/alvseek/agent-memory-system); the memory half is [Munnin](https://github.com/alvseek/agent-memory-server), and a coding client connects both.

Python · FastMCP · Starlette. Apache-2.0. Local only.

## What it serves

Seven of the overlay's nine procedures, composed exactly as the overlay's own compiler composes them: component references inlined at their reference point, then the `## Storage Mechanics` section swapped for the backend document's concrete ops. A served Prompt and an installed slash command are the same instruction, which is the property this pipeline exists to hold.

The two that are absent are `push-all` and `pull-all`. They compose the memory store's push and pull, and the memory write is durable on every insert, so there is nothing left for them to do here.

## Running it

Needs `uv` and `git`.

```sh
git clone --recurse-submodules https://github.com/alvseek/hermod-server
cd hermod-server
uv sync
uv run python -m hermod        # 127.0.0.1:8210
```

Connect a client:

```sh
claude mcp add --transport http hermod http://127.0.0.1:8210/mcp
```

or in a project's `.mcp.json`:

```json
{ "mcpServers": { "hermod": { "type": "http", "url": "http://127.0.0.1:8210/mcp" } } }
```

`/health` answers on the same process. There is no sign-in and no mode to choose: Hermod reads the repository on this machine, so it is local by construction. Every `HERMOD_*` setting is documented in [.env.example](.env.example).

## Status

Scaffold. The composition pipeline, the MCP surface and the project store are built in that order; [ADR-024](https://github.com/alvseek/agent-memory-project/blob/main/docs/adr/2026-10-12-hermod-doc-extraction-and-hermod-server.md) records what this server is for, and what it deliberately is not.

## License

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
