# Multi-stage build: one image, two entrypoints (trader daemon / monitor UI).
# Entrypoints are chosen by docker compose `command:` overrides.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY system ./system
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim-bookworm

# ta-lib >= 0.7 ships manylinux wheels that bundle the native TA-Lib library,
# so no source build is needed. (Fallback if a wheel is ever unavailable:
# install build-essential + wget here and build ta-lib 4.x from source before
# `uv sync`.)
RUN useradd --create-home --uid 1000 app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY system ./system

# Writable dirs for the non-root user: streamlit secrets (materialized from
# env at runtime) and the default data dir (usually shadowed by a bind mount
# whose ownership comes from the host).
RUN mkdir -p /app/.streamlit /app/data && chown -R app:app /app/.streamlit /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

USER app
