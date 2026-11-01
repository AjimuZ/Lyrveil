# ADR 0005: GPU exclusivity through container stop and start

Status: accepted (2026-11-01). Supersedes: none.

## Context
Both models must never hold VRAM at the same time. Rocinante needs about 9 GiB and SDXL several more, on a 12 GB card that the desktop also uses. KoboldCpp offers `--admin` to swap configurations, and ComfyUI offers `POST /free`. Compose down is forbidden for switches.

## Decision
Switch by stopping one container and starting the other through the Docker Engine API. The containers are pre-created by `docker compose create`, with fixed names. `LOADING_*` is only reachable from `IDLE`, and `IDLE` only after a verified release. Readiness uses Docker health checks.

## Alternatives
- KoboldCpp admin mode and ComfyUI `/free`: faster, but they rely on the process really freeing all memory, and a process remains in the GPU's way. Stopping the process is the strongest guarantee available.
- Both processes running, with models loaded on demand: a bug would break exclusivity silently.
- `docker compose up` and `stop` from a script: it would need Compose in the orchestrator and the project files, and is broader than two API calls.

## Consequences
Switches are slower: the planning target is under 150 s for a round trip, measured in P06. Scheduling batches work to limit switches. Settings changes use launch files, because stopped containers keep their command. Exclusivity is simple to test with state data alone.
