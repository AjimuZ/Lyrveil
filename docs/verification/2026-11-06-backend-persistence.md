# Backend persistence and health routes, 2026-11-06

Scope: roadmap item P01.3b, the SQLite engine with the WAL and FTS5 check, the Alembic baseline, the health routes and the backend part of `test integration` ([Backend](../backend.md#database-and-health), [Testing](../testing.md#commands)).

Machine: Windows 11 Pro, Docker Engine 29.9.2. Commands ran from Git Bash in the repository root.

## Lock and images

| Command | Result |
| --- | --- |
| `docker build -f backend/Dockerfile --target lockfile --output type=local,dest=backend .` | uv 0.13.6: `Resolved 37 packages`. Adds SQLAlchemy 2.1.6, Alembic 1.20.1 and their dependencies to `backend/uv.lock`; no existing pin changed |
| `docker build -q -f backend/Dockerfile -t lyrveil/backend:local .` | Built the runtime stage, which now holds `alembic.ini` and `migrations/`. User `10001:10001`, working directory `/app` |

## Results

| Command | Exit | Result |
| --- | --- | --- |
| `scripts/lyrveil.cmd test integration` | 0 | pytest 9.2.0 in the backend test stage, without network: 20 passed, 1 warning |
| `scripts/lyrveil.cmd test unit tests/unit` | 0 | 39 passed, 1 warning (31 before this task, 8 added) |
| `scripts/lyrveil.cmd test lint` | 1 | Run before this file existed: three links to it were broken. Ruff, format, `mypy --strict` and PSScriptAnalyzer already passed |
| `scripts/lyrveil.cmd test lint` | 0 | 77 Markdown files, 650 links and anchors, 0 broken. PSScriptAnalyzer: 0 findings. `ruff check`, `ruff format --check` and `mypy --strict` (22 source files) exit 0 |

The warning is Starlette's notice that its test client will move from `httpx` to `httpx2`.

The 20 integration tests cover:

- WAL: a new database file reports `wal`, the `-wal` file appears after a write, every connection carries the five documented pragmas, and an in-memory database, which cannot use WAL, is refused with `DB_CAPABILITY_MISSING`;
- FTS5: the compile option is present, a virtual table is created in the temporary database, and a stemmed text with an accent is found without it;
- the write option: with `BEGIN IMMEDIATE` a second writer fails at once with a lock error, and with the default `BEGIN` it does not;
- migrations: `alembic -x database=<file> upgrade head` run from an unrelated folder creates `alembic_version` at `0001` and `application_settings`, leaves the file in WAL, runs twice without change, and `downgrade base` removes the table; the JSON check constraint refuses text that is not JSON; `upgrade_to_head` and `migration_state` agree on an engine;
- health: `GET /api/v1/health/live` answers 200 with no database, `GET /api/v1/health/ready` answers 200 with every check on a migrated database, 503 with migrations pending, without WAL and without a database, and reports an unreachable control API without leaving ready;
- startup: `main()` migrates a new database and serves a ready application, and returns 69 without serving when WAL is missing or the data folder cannot be created.

The 8 new unit tests cover each refusal of the capability rule with scripted answers, and the control API probe against a local server (200, another status, a closed port).

## Not executed

- The runtime image was built but not started, because containers run only through the `test` levels. The integration tests run in the test stage, which has the same Python 3.12.16 and Debian trixie SQLite build as the runtime stage; no check compared the two SQLite builds directly.
- Reading the database on a Docker volume, the read-only root file system and the Compose health check do not exist yet; they belong to P01.2 and the `docker` level.
- `ready` against a live orchestrator: the control API probe was tested against a local stand-in only.
- The Pester tests, including the new case for `test integration` in a fixture without a Python component.
