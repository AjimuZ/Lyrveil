# Builds the orchestrator test stage from the repository root:
#   docker build -f orchestrator/Dockerfile --target test -t lyrveil/orchestrator-test:local .
# Regenerates orchestrator/uv.lock after a change to orchestrator/pyproject.toml, since uv is not installed on Windows:
#   docker build -f orchestrator/Dockerfile --target lockfile --output type=local,dest=orchestrator .
# Every installed package comes from uv.lock: "uv sync --locked" refuses a lock that does not match pyproject.toml.
# The runtime stage runs main.py, which serves the control API of api.py on port 8100.

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
# The fakes of tests/fakes and the contract page that their endpoint test reads. Only this test stage holds them.
COPY tests/fakes/*.py ./fakes/
COPY docs/runtime-contracts.md ./docs/runtime-contracts.md
CMD ["pytest"]

# The orchestrator of compose.test.yaml only, tagged "lyrveil/orchestrator-fakes:test": the runtime's packages and
# source plus the fakes of the container gate and the GPU probe, started by fakes.fake_orchestrator. compose.yaml never
# names this stage and the runtime stage copies nothing from it, so the fakes cannot reach a production image.
FROM base AS fake-runtime
ENV PYTHONPATH=/app/src:/app
COPY --from=deps /app/.venv /app/.venv
COPY orchestrator/src ./src
COPY tests/fakes/__init__.py tests/fakes/fake_docker.py tests/fakes/fake_nvml.py tests/fakes/fake_orchestrator.py ./fakes/
RUN python -m compileall -q src fakes
USER 10001:10001
EXPOSE 8100
CMD ["python", "-m", "fakes.fake_orchestrator"]

# The image of compose.yaml, selected there with "target: runtime". It holds no tests, no fakes and no development
# packages. Its command serves the control API over the Docker socket and NVML; the preflight runs as a one-off with
# "python -m lyrveil_orchestrator.preflight". It stays the last stage, so a build without --target gets it.
FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
COPY orchestrator/src ./src
RUN python -m compileall -q src
USER 10001:10001
EXPOSE 8100
CMD ["python", "-m", "lyrveil_orchestrator.main"]
