# ADR 0041: Shutdown drain through the database, and lifecycle events copied at each new GPU state

Status: accepted (2026-11-07). Supersedes: none. Refines [ADR 0004](0004-single-sqlite-writer.md) and
[ADR 0010](0010-job-queue-and-scheduling-policy.md).

## Context
- [Safe shutdown](../gpu-orchestration.md#safe-shutdown) asks `stop.cmd` to pause the backend's queue and let a running
  reply finish for up to 60 s, then to send `POST /v1/drain` and wait for the release. `stop.cmd` runs on Windows. The
  backend publishes no port, and the orchestrator publishes none either.
- The command that pauses the queue, `python -m lyrveil.cli drain`, runs as its own process inside the backend container,
  apart from the server process that runs the scheduler.
- The orchestrator keeps its events in memory, the newest 5000 of its process. The backend, the only SQLite writer,
  must store them in `model_lifecycle_events` without losing or doubling any when it restarts.
- A run cut by the backend's shutdown kept its lease until the 30 s TTL, so a drain in the orchestrator waited that long
  before it could stop the runtime.

## Decision
- **The pause lives in the database.** `drain` sets the `jobs.paused` row of `application_settings` and waits until no
  job is running. The scheduler reads the row at each pass and starts nothing. `JobQueue.start` also checks it in the
  statement that marks a job running, so no job starts after the drain has seen the queue empty. Every backend start
  clears the row, and `python -m lyrveil.cli resume` clears it after a stop that failed.
- **The GPU drain goes through the backend.** `python -m lyrveil.cli drain-gpu` runs in the backend container, which has
  the control token, sends `POST /v1/drain` and long-polls the state until `IDLE`, `ERROR` or 90 s.
- **A shutdown hands the GPU back.** When the worker is cancelled during a GPU stage, the scheduler aborts the run's
  generation and releases its lease with `ok`, shielded from the cancellation. The job stays `running`, and the next
  start recovers it as before: a reply becomes `interrupted`, any other job is queued again.
- **Events are copied at each new GPU state.** `GpuStateFollower` already long-polls the state, and every new `seq` comes
  with a new event. Its observer, `LifecycleEventRecorder`, reads `GET /v1/events` after the newest stored event and
  stores the new ones in one transaction. `(boot_id, seq)` is unique, so an event read twice is stored once.

## Alternatives
- **A drain route on the backend's HTTP API.** It would need an exception to the Origin check for writes, and nginx would
  expose it to the browser. The database is already shared by both processes of the container.
- **A signal or a file in the data volume.** A signal reaches only the server process, and a file would need its own
  cleanup and permissions. A row is written in a transaction and read by the same statement that starts a job.
- **`stop.cmd` calls the orchestrator itself.** It would need the token on the command line and a published port.
- **A separate loop that polls `GET /v1/events`.** It would add a second timer and duplicate the follower's resync.
  An event that the follower's state does not reveal is copied with the next state.

## Consequences
- A queue left paused by a stop that failed makes the app accept messages and answer none until `resume` or the next
  start. `stop.cmd` runs `resume` when `docker compose stop` fails after a drain.
- The drain does not stop a running job: past 60 s, `docker compose stop` cuts it. The next start marks a cut reply
  `interrupted`, and queues any other job again with the cut run counted as an attempt.
- Events the orchestrator dropped before the backend read them, such as those of an orchestrator process that ended
  while the backend was down, are lost. The recorder logs the size of the gap.
- `vram_total_mib` stays `NULL`, because no event carries the card's total yet.
