# Control API, GPU monitor and preflight, 2026-11-06

Scope: roadmap item P01.4c, `orchestrator/src/lyrveil_orchestrator/gpu_monitor.py`, `preflight.py` and `api.py`, their dependencies, the unit tests of the preflight thresholds and the T-ORC-10 integration tests ([APIs](../api.md#control-api), [Operations](../operations.md#preflight-checks)).

Machine: Windows 11 Pro. Commands ran from Git Bash in the repository root.

## Lock and image

| Command | Result |
| --- | --- |
| `docker build -f orchestrator/Dockerfile --target lockfile --output type=local,dest=orchestrator .` | uv 0.13.6 on CPython 3.12.16 wrote `orchestrator/uv.lock`. A first run resolved Pydantic 2.14.3, so `constraint-dependencies = ["pydantic==2.14.2"]` was added to `[tool.uv]` and the build ran again. The lock now holds Pydantic 2.14.2 and pydantic-core 2.50.2, the versions of the backend's lock |

The lock adds FastAPI 0.162.0 and nvidia-ml-py 13.615.71 as runtime dependencies, as in [Technology](../technology.md#backend-and-orchestrator). FastAPI brings Starlette 1.10.0, Pydantic, annotated-doc, annotated-types, opentelemetry-api 1.45.2, typing-extensions and typing-inspection. Pydantic is a constraint, not a direct dependency. nvidia-ml-py has no types and no `py.typed` marker, so `pyproject.toml` lets mypy skip the `pynvml` import, and the probe converts every value it reads into a typed one.

## Results

| Command | Exit | Result |
| --- | --- | --- |
| `scripts/lyrveil.cmd test lint`, last run after these documents | 0 | 80 Markdown files, 683 links and anchors, 0 broken, 9 JSON samples parse. PSScriptAnalyzer: 7 scripts, 0 findings. Backend gates exit 0, 23 source files. Orchestrator: `ruff check`, `ruff format --check` and `mypy --strict src tests` exit 0, 13 source files |
| `scripts/lyrveil.cmd test unit tests/unit/test_gpu_monitor.py tests/unit/test_preflight.py tests/unit/test_locked_environment.py tests/unit/test_socket_confinement.py` | 0 | Backend skipped, since it holds none of the first three files. Orchestrator: 55 passed in 0.45 s |
| `scripts/lyrveil.cmd test integration` | 0 | Backend: 20 passed. Orchestrator: `pytest tests/integration`, 198 passed in 1.34 s. Each component prints one Starlette warning that its test client uses `httpx`, which is deprecated |

T-ORC-10 is covered by `orchestrator/tests/integration/test_control_api.py`:

- 16 method and path pairs, every route of the control API table delivered or not plus `POST /v1/health`, `/v1/health/`, an unknown path, `/openapi.json`, `/docs` and `/`, each with 11 wrong `Authorization` values, answer `401` with `WWW-Authenticate: Bearer` and a problem document with code `UNAUTHORIZED`. The wrong values are: no header, an empty one, the scheme alone, an empty token, another token, the token without its last character, with one more, in upper case, under the `Basic` scheme, without a scheme, and with a byte outside ASCII;
- `GET /v1/health` answers `200` with every one of those values and with the right token;
- the right token, with the scheme in any case and extra spaces, reaches `GET /v1/preflight`, which answers `200` with the four rows. An undelivered route, `/openapi.json` and `/docs` answer `404` to it;
- with `hmac.compare_digest` replaced by a spy, a wrong token and a missing header are both compared through it, as bytes against the configured token, so no other comparison decides;
- `create_app` refuses an empty token, the `.env.example` placeholder, and tokens that are too short, too long or not hexadecimal, with a message that does not hold the value.

`orchestrator/tests/unit/test_preflight.py` judges a fake `GpuProbe` at each threshold of [Operations](../operations.md#preflight-checks): CUDA 12.9 fails and 13.0, 13.2 and 14.0 pass; total VRAM passes at 11000 and 12282 MiB, warns at 10999 and 10000 MiB, and fails at 9999 and 8192 MiB; free VRAM passes at 9000 MiB and above, and only warns below it, with the waiting note under 6000 MiB. An NVML failure fails all four rows and names the error. The command exits 0 with warnings and 1 with a failure, prints the report as JSON with `--json`, and closes the monitor either way.

`orchestrator/tests/unit/test_gpu_monitor.py` replaces the functions of `pynvml`: it checks the decoding of NVML's CUDA version, MiB rounded down, a missing library raised as `GpuProbeError` and retried on the next read, a failed device lookup that shuts NVML down again, and init and shutdown paired once.

`test_socket_confinement.py` still finds only `container_gate.py`: the three new modules hold nothing that could open the Docker socket.

## Not executed

| Item | Reason | Follow-up |
| --- | --- | --- |
| `python -m lyrveil_orchestrator.preflight` against the real card | Running a container with the GPU is outside the Docker commands this session could use, and Compose does not define the orchestrator service yet | P01.6, when `start.cmd` runs it through Compose |
| The Docker daemon and Engine API rows of the preflight from inside the orchestrator | The container gate allows no version call, and `start.cmd` checks both on the host | P01.6 |
| Serving the control API on port 8100 | No `main.py`, Uvicorn dependency or `runtime` stage exists yet | The item that adds the orchestrator service to Compose |
| The full `scripts/lyrveil.cmd test unit` | Only the files above ran | Next full run of the level |
