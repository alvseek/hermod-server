# Hermod — the coding overlay's MCP server

Hermod serves the [agent-memory-project](https://github.com/alvseek/agent-memory-project) overlay — the coding shell, project memory and push/pull — over MCP, so a client that cannot install markdown can still use it. It is the coding half of the [agent-memory framework](https://github.com/alvseek/agent-memory-system); the memory half is [Munnin](https://github.com/alvseek/agent-memory-server), and a coding client connects both.

Python · FastMCP · Starlette. Apache-2.0.

## What it serves

Eight of the overlay's ten procedures, composed exactly as the overlay's own compiler composes them: component references inlined at their reference point, then the `## Storage Mechanics` section swapped for the backend document's concrete ops. A served Prompt and an installed slash command are the same instruction, which is the property this pipeline exists to hold.

The two that are absent are `push-all` and `pull-all`. They compose the memory store's push and pull, and the memory write is durable on every insert, so there is nothing left for them to do here.

Alongside the content it owns **the project store** — context entries and the orientation map — and registers one tool per `db.md` op, because those tools write and a store the overlay's procedures address has to be reachable from the same face.

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

`/health` answers on the same process. With no issuer configured the server is local by construction: it binds loopback, asks nobody to log in, and refuses to start if the bind address or the public base URL says something else could reach it. Every `HERMOD_*` setting is documented in [.env.example](.env.example).

## Hosting it

Set an issuer and the MCP face requires a token the issuer minted:

```sh
HERMOD_LOGTO_ENDPOINT=https://auth.lok.quest
HERMOD_PUBLIC_BASE_URL=https://hermod.lok.quest
HERMOD_HOST=0.0.0.0
uv run python -m hermod
```

There is no mode to switch: the issuer's presence *is* the switch, so tokens are verified exactly when there is something to verify them against. `HERMOD_PUBLIC_BASE_URL` is the resource identifier clients are sent to log in for, and it has to match the API resource registered with Logto character for character or every token is refused as `invalid_target`. `/.well-known/oauth-protected-resource/mcp` is served from the root so a client can discover the issuer, and `/health` stays open because a deploy's health gate calls it without a credential.

Hermod has no tenancy. Munnin keys its store by the caller's `(iss, sub)`; Hermod's is keyed by project, so a verified token is a gate and never a partition.

The deployment lives in a separate private repo, `hermod-deploy`, which pins this one as a submodule. Its CI publishes `ghcr.io/alvseek/hermod` tagged by commit SHA and deploys a tag that already exists.

## The store

Hermod owns project-keyed memory: context entries and the orientation map. They live in the database the memory store already uses, so there is one engine and one place, and `HERMOD_DB_URL` is the only way to name it. There is no file backend and no switch: the server refuses to start without a DSN rather than serving under a guess.

Munnin starts that database and Hermod only consumes it, which is why no compose lives in this repo. Migrations apply lazily on the store's first connection, so a fresh database needs no separate step.

## Status

Local and hosted forms both work, and the store runs on both engines. The composition pipeline, the MCP surface and the project store were built in that order. [ADR-024](https://github.com/alvseek/agent-memory-project/blob/main/docs/adr/2026-10-12-hermod-doc-extraction-and-hermod-server.md) records what this server is for, [ADR-025](https://github.com/alvseek/agent-memory-project/blob/main/docs/adr/2026-10-12-hermod-auth-and-hosting.md) records why it grew a guarded, hosted shape, and [ADR-026](https://github.com/alvseek/agent-memory-project/blob/main/docs/adr/2026-10-12-hermod-store-on-the-shared-instance.md) records where its store lives.

## License

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
