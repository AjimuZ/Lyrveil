# Builds the orchestrator test stage from the repository root:
#   docker build -f orchestrator/Dockerfile --target test -t lyrveil/orchestrator-test:local .
# Regenerates orchestrator/uv.lock after a change to orchestrator/pyproject.toml, since uv is not installed on Windows:
#   docker build -f orchestrator/Dockerfile --target lockfile --output type=local,dest=orchestrator .
# Every installed package comes from uv.lock: "uv sync --locked" refuses a lock that does not match pyproject.toml.
# The runtime stage comes with main.py, which will serve the control API of api.py on port 8100.

FROM python:3.12.16-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app

FROM base AS uv
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

FROM uv AS lock
COPY orchestrator/pyproject.toml ./
RUN uv lock

FROM scratch AS lockfile
COPY --from=lock /app/uv.lock /uv.lock

FROM uv AS deps
COPY orchestrator/pyproject.toml orchestrator/uv.lock ./
RUN uv sync --locked --no-dev --compile-bytecode

# Lint, type checks and tests run in containers of this stage, started by scripts\lyrveil.cmd test.
FROM deps AS test
RUN uv sync --locked
COPY orchestrator/src ./src
COPY orchestrator/tests ./tests
CMD ["pytest"]
