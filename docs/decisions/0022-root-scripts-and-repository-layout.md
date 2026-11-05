# ADR 0022: Thin root scripts, implementations in `scripts/`

Status: superseded by [ADR 0029](0029-windows-operation-with-docker-volumes.md) on 2026-11-05. Accepted 2026-11-01. Supersedes: none.

## Context
The requirements say that the root must hold `start.sh`, `stop.sh`, `restart.sh`, `status.sh`, `logs.sh` and `setup.sh`. The suggested tree also lists them under `scripts/`, and allows refinement. Shared code and helper scripts need a home.

## Decision
- Root files are thin entry points. Each one changes to the repository root and `exec`s `scripts/<name>.sh`, with its arguments.
- `scripts/` holds the implementations, `lib/common.sh`, `preflight.sh`, `download-models.sh`, `backup.sh`, `restore.sh` and `test.sh`.
- The `tests/` directory at the root holds cross-service tests and fakes. Each package keeps its own unit tests next to it.
- All Docker build contexts are the repository root, so one `.dockerignore` guards everything.
- `orchestrator/` is a top-level package, separate from `backend/`.

## Alternatives
- Implementations directly in the root: a crowded root, and no shared library.
- Symlinks from the root: fragile on Windows checkouts.
- A `Makefile`: another tool, while the requirements ask for these exact scripts.
- Per-service build contexts: smaller contexts, but several ignore files to keep correct. BuildKit only sends what `COPY` needs, so one root context costs little.

## Consequences
`./start.sh` works as required, and the code stays organized. A small test verifies that root files only delegate.
