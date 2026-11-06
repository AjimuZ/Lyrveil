# Core Compose services, 2026-11-06

Task P01.2, from Windows with Docker Desktop. Only what was run is listed.

| Check | Result |
| --- | --- |
| `docker compose config --quiet` with no `.env` file present | Passed, no output |
| `docker compose build` | Passed: `lyrveil/backend:local`, `lyrveil/orchestrator:local` and `lyrveil/frontend:local` built. The backend and frontend layers were mostly cached; the orchestrator `runtime` stage was built for the first time |
| Both volumes declared `external: true` | By reading `compose.yaml`; `config` does not check that the volumes exist |
| `.gitattributes` | Read: `*.cmd`, `*.ps1` and `*.psm1` use CRLF, everything else LF. Unchanged, since P00.8 already holds the rules |

Not verified:

- No container was started. The health checks, `gpus: all`, the Docker socket mount, the read-only root and the
  network isolation have not run.
- The orchestrator image cannot serve yet: `lyrveil_orchestrator.main` does not exist (P02).
- `.env.example` was written by the supervising session from [Example `.env`](../configuration.md#example-env),
  because task sessions cannot write `.env*` files. This session did not read or edit it, so its content is not
  verified here. It should list the eight variables of the table in
  [Deployment settings](../configuration.md#deployment-settings), which are all the backend reads
  (`backend/src/lyrveil/settings.py`); the orchestrator code reads no variable of its own yet.
- `docker compose config --quiet` was run again at the end of the session and passed.
