# Builds the backend image and its test stage from the repository root:
#   docker build -f backend/Dockerfile -t lyrveil/backend:local .
#   docker build -f backend/Dockerfile --target test -t lyrveil/backend-test:local .
# Regenerates backend/uv.lock after a change to backend/pyproject.toml, since uv is not installed on Windows:
#   docker build -f backend/Dockerfile --target lockfile --output type=local,dest=backend .
# Every installed package comes from uv.lock: "uv sync --locked" refuses a lock that does not match pyproject.toml.

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
COPY backend/pyproject.toml ./
RUN uv lock

FROM scratch AS lockfile
COPY --from=lock /app/uv.lock /uv.lock

FROM uv AS deps
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --compile-bytecode

# Lint, type checks and tests run in containers of this stage, started by scripts\lyrveil.cmd test.
FROM deps AS test
RUN uv sync --locked
COPY backend/src ./src
COPY backend/tests ./tests
CMD ["pytest"]

FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
COPY backend/src ./src
RUN python -m compileall -q src
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "lyrveil.main"]
