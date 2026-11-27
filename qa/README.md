---
doc_type: qa-riao-readme
---

# Hermod procedure QA bench

Run the packaged Hermod server on `127.0.0.1:8210` and read a composed procedure from each real content checkout. Other clients, including Asgard, can use the same running `/mcp` endpoint.

## The R/I/A/O Loop

| Phase | Mechanism | Result | Status |
|---|---|---|---|
| Reset | [QA Compose project](compose.yaml), `down` | Removes only this project's container and network; no store reset. | Documented |
| Inject | [Product Dockerfile](../Dockerfile), `build` | Packages both [content roots](seeds/qa-seeds-map.md) from this checkout. No database seed. | Documented |
| Act | [QA Compose project](compose.yaml), `up --wait` | Starts the service and waits for its container health check. | Documented |
| Observe | [MCP smoke](scripts/smoke.py) | Checks HTTP identity, reads `awaken-coder` and `high-wizard` and checks for expanded content. Nonzero exit on failure. | Documented |

## Daily loop

From the **Hermod server repository root**, with its submodules initialized, Docker Compose v2, and `uv`:

```sh
docker compose -p hermod-qa -f qa/compose.yaml up --build --wait
uv run --locked --no-dev python qa/scripts/smoke.py
```

Rebuild after edits to either content root. Leave the project running for Asgard. To stop or reset it, use `docker compose -p hermod-qa -f qa/compose.yaml down`, then repeat the daily loop when needed.

## Scope and evidence

The [QA configuration](config/REQUIRED.md) is content-only: neither a live PostgreSQL instance nor a login is needed for these procedure reads. This does not verify tenant or database behavior. The store test suite uses its own disposable database.

On 2026-11-27 the smoke passed through the published port, failed after `down`, and passed again after `up`. Munnin's `/health` still answered while Hermod was stopped. See the [runbook](runbooks/hermod.md) for diagnosis and the [inventory](qa-map.md) for test boundaries.
