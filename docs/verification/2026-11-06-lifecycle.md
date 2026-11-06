# Lifecycle commands, 2026-11-06

Task P01.6b, from Git Bash on Windows 11 with Docker Desktop, on 2026-11-06 and 2026-11-07. Only what was run is
listed as run.

## Written

- `start.cmd`, `stop.cmd`, `restart.cmd`, `status.cmd` and `logs.cmd` at the root, each a comment, the call to
  `scripts\lyrveil.ps1 <command> %*` and the exit, like `setup.cmd`.
- `scripts/lib/Lifecycle.psm1` and the five commands in `scripts/lyrveil.ps1`. `scripts/lib/Setup.psm1` now also
  exports the helpers that the lifecycle commands reuse: native command calls, the preflight facts and report, the
  Compose arguments and configuration, the project's volumes and images.
- `tests/docker/Lifecycle.Tests.ps1`, `tests/docker/compose.lifecycle-test.yaml`, the override of the test project
  `lyrveil-lifecycle-test`, and `tests/docker/compose.lifecycle-unhealthy.yaml`, which makes the backend's health
  check fail.

## Run

- `scripts/lyrveil.cmd test docker`: passed, 57 tests, 0 failed, in 103.29 s, with Pester 5.7.1. The first run had 10
  failures with two causes: a mandatory parameter of `Lifecycle.psm1` refused the empty warning list, so every start
  failed and the tests that need running services failed after it (fixed with `[AllowEmptyCollection()]`), and
  PSScriptAnalyzer flagged a test helper named with the verb `Remove` (renamed). The three runs after the fixes passed. In the passing runs, against `lyrveil-lifecycle-test` with the real engine:
  - T-DCK-01: with no `.env`, volumes or containers, start listed `.env` and the volumes as missing, ran setup, printed
    the data and models volume rows as `PASS`, and ended with `Lyrveil è pronto: http://localhost:14210` as its last
    line. A second start ran no setup and printed the warnings for the model files, the text model and the
    orchestrator, with exit status 0.
  - T-DCK-05: the backend and the frontend were `running healthy`, and `GET http://127.0.0.1:14210/` answered 200. With
    `compose.lifecycle-unhealthy.yaml` added, start exited 3, printed `FAILURE  Health checks`, the last 50 log lines
    and `exit status 3`, and no ready line.
  - status printed both services as `running, healthy`, the orchestrator as `missing`, the URL as answering and
    `Lyrveil: ready`. `status -Json` gave `ready: true`, `app_answers: true`, the services, `null` for the GPU and the
    active model, the free disk and the warnings. After stop, status printed `Lyrveil: not ready`.
  - logs with a service, `-Tail` and `-Since` exited 0; `-Since` without a value and an unknown service exited 1.
  - T-DCK-02: stop left both containers `exited` and both volumes in place, and a second stop also exited 0. With a
    stand-in `docker.cmd` that always fails, stop exited 0, and start, status, `status -Json` and logs exited 2.
  - T-DCK-03: restart from stopped and from running services exited 0 and ended with the ready line.
  - T-DCK-04: a file written into the data volume, and the backend's `db/lyrveil.sqlite3`, were still there after the
    containers were removed and start created new ones, and again after the three test images were removed, so that
    start ran setup, which built them again (`Images: built`).
  - Each root `.cmd` file with an unknown argument exited 1 through `cmd.exe`, and `Setup.Tests.ps1` found all six root
    `.cmd` files delegating to a command that `scripts\lyrveil.ps1` handles. PSScriptAnalyzer found nothing.
  - Afterwards the test project and both test volumes were gone, as the test's `AfterAll` checks.
- `scripts/lyrveil.cmd test lint`: passed. Before this file existed it failed only on the three links to it.

## Not run

- `start.cmd` and the other commands against the real project `lyrveil`, so the ready line on
  `http://localhost:4200` has not been seen on a real installation. The tests use port 14210.
- The GPU rows of start (`docker compose run --rm --no-deps -T lyrveil-orchestrator python -m
  lyrveil_orchestrator.preflight`). The tests skip them, so a failure there, and its exit status 2, are unverified.
- The 120 s limit of a real start. The failure test used 60 s, and the backend became unhealthy before that.
- `logs.cmd -Follow`, which runs until it is interrupted.
- The steps that need components that do not exist yet: the model file check, the wait for `LLM_READY`, the queue
  drain, `POST /v1/drain` and the backup copy. Each prints a warning instead.
