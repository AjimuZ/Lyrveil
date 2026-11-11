# Builds the backend image and its test stage from the repository root:
#   docker build -f backend/Dockerfile -t lyrveil/backend:local .
#   docker build -f backend/Dockerfile --target test -t lyrveil/backend-test:local .
# Regenerates backend/uv.lock after a change to backend/pyproject.toml, since uv is not installed on Windows:
#   docker build -f backend/Dockerfile --target lockfile --output type=local,dest=backend .
# Regenerates backend/openapi.json, the contract the frontend generates its types from, after an API change:
#   docker build -f backend/Dockerfile --target openapi --output type=local,dest=backend .
# Every installed package comes from uv.lock: "uv sync --locked" refuses a lock that does not match pyproject.toml.

FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
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
COPY backend/alembic.ini ./alembic.ini
COPY backend/openapi.json ./openapi.json
COPY backend/migrations ./migrations
COPY backend/src ./src
COPY backend/tests ./tests
# The catalog and the page it must match, which tests/unit/test_catalog.py compares.
COPY config/model-catalog.json ./config/model-catalog.json
COPY docs/models.md ./docs/models.md
# The workflow templates that lyrveil.images.workflows fills and tests/unit/test_workflows.py loads.
COPY workflows/comfyui ./workflows/comfyui
# The fakes of tests/fakes and the contract page that the runtime adapter's tests read. Only this test stage holds them.
COPY tests/fakes/*.py ./fakes/
COPY docs/runtime-contracts.md ./docs/runtime-contracts.md
CMD ["pytest"]

# Writes the OpenAPI document of the sources, not the committed one, so the output reflects the current API.
FROM deps AS openapi-build
COPY backend/src ./src
RUN python -m lyrveil.openapi_document /app/openapi.json

FROM scratch AS openapi
COPY --from=openapi-build /app/openapi.json /openapi.json

FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY backend/src ./src
# The models tool reads the catalog from here (lyrveil.tools.models.DEFAULT_CATALOG).
COPY config/model-catalog.json ./config/model-catalog.json
# The image job fills these templates (lyrveil.images.workflows.workflows_dir).
COPY workflows/comfyui ./workflows/comfyui
RUN python -m compileall -q src
# A new named volume takes the owner of the folder it is mounted on, so /data must exist and belong to the service user
# in the image; otherwise the volume is owned by root and the backend cannot create /data/db.
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "lyrveil.main"]
