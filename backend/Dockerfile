# syntax=docker/dockerfile:1
#
# Build from the repository root so the license (referenced by pyproject.toml) is in context:
#   docker build -f backend/Dockerfile -t voice-release-assistant .

ARG PYTHON_VERSION=3.14

# --- build: resolve and install dependencies into a virtualenv ---------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY LICENSE ./
WORKDIR /app/backend

# Dependencies first: this layer is rebuilt only when the lock file changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then the application itself, installed as a regular (non-editable) package.
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# --- runtime: only the virtualenv, no build tools, no root --------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# A fixed, unprivileged identity. The service writes nothing to disk, so it needs no home.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# Same path as in the builder: the virtualenv's scripts contain absolute paths.
COPY --from=builder --chown=app:app /app/backend/.venv /app/backend/.venv

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RELEASE_ASSISTANT_HOST=0.0.0.0

USER app
EXPOSE 8000

# Liveness only: /healthz is unauthenticated and exempt from rate limits.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('RELEASE_ASSISTANT_PORT', '8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2).status == 200 else 1)"]

CMD ["release-assistant"]
