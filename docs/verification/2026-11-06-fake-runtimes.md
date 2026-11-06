# Fake runtimes, 2026-11-06

Task P01.8b. Only what was run is listed as run.

## Written

- `tests/fakes/`: `scripted_server.py` (the shared server, scripts and WebSocket frames), `fake_kobold.py`,
  `fake_comfy.py`, `fake_docker.py`, `fake_nvml.py` and `Dockerfile` for the two HTTP fakes.
- `compose.test.yaml`: project `lyrveil-test`, port `LYRVEIL_TEST_HTTP_PORT` (14200), its own volumes, `:test` tags.
- Tests in `orchestrator/tests/unit/`: `test_fake_kobold.py`, `test_fake_comfy.py`, `test_fake_gate_and_probe.py`
  and `test_fake_endpoints.py`, which compares the routes of the HTTP fakes with the tables of
  [Runtime contracts](../runtime-contracts.md).
- `tests/docker/TestFakes.Tests.ps1` and the `docker` level in `scripts/lib/TestCommand.psm1`: the file systems of the
  three production images must hold nothing of `tests/fakes/`, and the resolved `compose.test.yaml` must name no
  external volume, host folder, Docker socket, GPU or port 4200.
- The orchestrator test stage copies `tests/fakes/*.py` and `docs/runtime-contracts.md`. `.dockerignore` re-includes
  both. `orchestrator/pyproject.toml` adds `.` to the pytest path, the mypy path and the ruff source roots.

## Run

- `scripts\lyrveil.cmd test lint` passed, including `ruff check`, `ruff format --check` and `mypy --strict` of the
  orchestrator test stage, which now holds `tests/fakes/` and `docs/runtime-contracts.md`. So the orchestrator image's
  test stage builds with the new `COPY` lines and `.dockerignore` re-includes both paths. An earlier run failed on one
  line over 120 characters and on `SIM113` in `fake_comfy.py`; both are fixed.
- `scripts\lyrveil.cmd test unit tests/unit/test_fake_comfy.py -q`: 13 passed.

## Not run

The session's shell allowed no Docker command other than the project checks above, so none of the following was run.
They are written and unverified:

- The pytest files `test_fake_kobold.py`, `test_fake_gate_and_probe.py` and `test_fake_endpoints.py`.
- `docker compose -f compose.test.yaml config`, the build of `lyrveil/fakes:test`, and starting the project.
- `tests/docker/TestFakes.Tests.ps1`, and `scripts\lyrveil.cmd test docker`.
- The new Pester and PSScriptAnalyzer findings of the `docker` level wiring.

Assumptions in the fakes, not confirmed against the real runtimes: the out-of-memory reply of `fake_kobold`; the field
`value` of `GET /api/extra/true_max_context_length`; the shape of `GET /system_stats` and of the `/history` answer of
`fake_comfy`. The GPU suite replaces them with recorded responses at P02 and P05.
