# Backend skeleton and gates, 2026-11-06

Scope: roadmap item P01.3a, the backend package, settings, logging, Dockerfile and its gates in `scripts\lyrveil.cmd test` ([Backend](../backend.md#quality-gates), [Testing](../testing.md#the-lint-level)).

Machine: Windows 11 Pro, Windows PowerShell 5.1, Docker Engine 29.9.2. Commands ran from Git Bash in the repository root.

## Lock and images

| Command | Result |
| --- | --- |
| `docker build -f backend/Dockerfile --target lockfile --output type=local,dest=backend .` | uv 0.13.6 on CPython 3.12.16: `Resolved 33 packages`. Wrote `backend/uv.lock` |
| `docker build -q -f backend/Dockerfile --target test -t lyrveil/backend-test:local .` | Built |
| `docker build -q -f backend/Dockerfile -t lyrveil/backend:local .` | Built the runtime stage |

The lock pins the direct dependencies of [Technology](../technology.md#backend-and-orchestrator): FastAPI 0.162.0, Pydantic 2.14.2, pydantic-settings 2.16.2, structlog 26.1.0, tzdata 2026.6, Uvicorn 0.54.0, and in the `dev` group HTTPX 0.28.1, mypy 2.4.1, pytest 9.2.0 and Ruff 0.17.3. It also resolved Starlette 1.10.0 and pydantic-core 2.50.2.

## Results

| Command | Exit | Result |
| --- | --- | --- |
| `scripts/lyrveil.cmd test lint` with a line of 122 characters in `logs.py` | 1 | `ruff check` exit 1 (E501), `ruff format --check` exit 1, `mypy --strict` exit 0 |
| `scripts/lyrveil.cmd test lint` after the fix, with these documents | 0 | 76 Markdown files, 642 links and anchors, 0 broken. PSScriptAnalyzer: 7 scripts, 0 findings. `ruff check`, `ruff format --check` and `mypy --strict src tests` (9 source files) exit 0 |
| `scripts/lyrveil.cmd test unit tests/unit` | 0 | pytest 9.2.0: 31 passed, 1 warning |
| `scripts/lyrveil.cmd test unit tests/unit/test_settings.py::test_a_missing_token_is_named -v` | 0 | 1 collected, 1 passed |
| `scripts/lyrveil.cmd test lint -k x` | 1 | `Test level 'lint' takes no arguments.` |
| `scripts/lyrveil.cmd test` | 1 | Usage message |
| `scripts/lyrveil.cmd test integration` | 1 | `Test level 'integration' has no tests yet.` |

The 31 backend unit tests cover:

- the import rules: no violation in the package, and a reported violation for each rule, with absolute and relative imports, including `domain` importing `settings`, `logs`, `services` and `persistence.models`;
- the settings: defaults, an invalid value for each variable named without its value, a missing token, immutability, and `main()` returning 78 with the variable name on stderr;
- logging: JSON fields, content fields dropped by name, free text in identifier fields dropped, exceptions without their message, standard library records, the level filter;
- the environment: every installed distribution has the version `uv.lock` pins;
- the application factory: OpenAPI at `/api/v1/openapi.json`, no `/docs` or `/redoc` pages.

The warning is Starlette's notice that its test client will move from `httpx` to `httpx2`. It does not affect the tests.

## Not executed

| Item | Reason | Follow-up |
| --- | --- | --- |
| The full `scripts/lyrveil.cmd test unit`, with the Pester tests | The session ran only the backend part. The two new Pester cases have not run yet | Next full run of `test unit` |
| Starting the runtime image, and its exit status 78 on an invalid setting | The session may not run containers outside the `test` levels. The unit test calls `main()` in the test stage | P01.3b health routes, then the Docker level |
| A check that the runtime image holds no tests or tools | Same reason | T-DCK-10 in P01 |
