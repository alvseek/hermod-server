# syntax=docker/dockerfile:1

# Hermod — the coding overlay's MCP server.
#
# Shape: inbound-serving (uvicorn on HERMOD_PORT), file-state (the project store, SQLite).
# The agent-memory-project submodule is NOT a separate unit — it is read-only content this
# server loads at runtime (served prompts, plus storage-backends/seam.py loaded by path), so
# it is copied in as content and never written to.

# ---- Build Stage ----
FROM python:3.12.14-slim-bookworm AS build

# Pinned to match uv.lock (revision 3); a newer uv may want to rewrite the lock,
# which --locked would then reject.
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /bin/uv

ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies before source, so this layer survives every code edit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/opt/uv-cache \
    uv sync --locked --no-dev --no-install-project

# README.md, LICENSE and NOTICE are required: pyproject declares
# `readme = "README.md"` and `license-files = ["LICENSE", "NOTICE"]`, and the
# uv_build backend refuses to build the project when any of them is missing.
COPY README.md LICENSE NOTICE ./
COPY src ./src
RUN --mount=type=cache,target=/opt/uv-cache \
    uv sync --locked --no-dev

# ---- Runtime Stage ----
FROM python:3.12.14-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMOD_HOST=0.0.0.0 \
    HERMOD_PORT=8210 \
    HERMOD_DB_PATH=/app/data/hermod.db \
    HERMOD_CONTENT_ROOT=/app/agent-memory-project

WORKDIR /app

RUN groupadd --system --gid 10001 hermod \
 && useradd --system --uid 10001 --gid hermod --no-create-home --shell /usr/sbin/nologin hermod

# The environment, then the source it points at. uv installs the project
# editable by default, so /app/src must exist at the same path it did at build
# time — which also keeps the data_entities/migrations/ SQL files resolvable via
# their __file__-relative load, exactly as they resolve under systemd.
COPY --from=build --chown=hermod:hermod /app/.venv /app/.venv
COPY --chown=hermod:hermod src ./src

# Served overlay content (the agent-memory-project submodule). Read-only at runtime.
COPY --chown=hermod:hermod agent-memory-project ./agent-memory-project

# Created here and owned by hermod so a FRESH named volume inherits this
# ownership instead of coming up root-owned and unwritable.
RUN install -d -o hermod -g hermod /app/data

USER hermod

EXPOSE 8210

# python is guaranteed present in the runtime image; curl is not, and a
# HEALTHCHECK invoking a missing binary reports unhealthy forever.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('HERMOD_PORT','8210')+'/health', timeout=2).status==200 else 1)"]

# Exec form, and the venv interpreter directly — the same shape as
# deploy/hermod.service's ExecStart would be, so signals reach uvicorn.
ENTRYPOINT ["/app/.venv/bin/python", "-m", "hermod"]
