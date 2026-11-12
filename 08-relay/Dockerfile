# One image for every Python service (API, workers, demo CRM, migrations).
# Each Compose service runs a different command.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first (cached unless the lock file changes); exact versions from uv.lock.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY alembic.ini ./
COPY relay ./relay
COPY services ./services
COPY migrations ./migrations
COPY fixtures ./fixtures

RUN useradd --create-home --uid 10001 relay
USER relay
