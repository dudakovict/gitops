# syntax=docker/dockerfile:1.7
# ---- builder: resolve the lockfile into a virtualenv -------------------------------
FROM docker.io/library/python:3.13.13-slim-trixie AS builder
# Apply Debian security updates so the image does not ship CVEs the base already fixed upstream.
RUN apt-get update && apt-get -y --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev
COPY docqa ./docqa
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# ---- runtime -----------------------------------------------------------------------
FROM docker.io/library/python:3.13.13-slim-trixie
RUN apt-get update && apt-get -y --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*
ARG VERSION=dev
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH" DOCQA_VERSION=${VERSION}
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/docqa ./docqa
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini ./
# OpenShift runs this as an arbitrary UID with GID 0; nothing here needs to be writable.
USER 1001
EXPOSE 8080
ENTRYPOINT ["docqa-api"]
