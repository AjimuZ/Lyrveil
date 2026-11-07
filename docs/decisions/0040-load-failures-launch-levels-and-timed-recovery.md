# ADR 0040: Load failures in the state document, launch levels in the backend, timed recovery

Status: accepted (2026-11-07). Supersedes: none. Refines [ADR 0005](0005-exclusive-gpu-by-container-lifecycle.md) and
[ADR 0038](0038-vram-hold-in-the-orchestrator-wait-in-the-backend.md).

## Context
- [Load failures and launch levels](../gpu-orchestration.md#load-failures-and-launch-levels) says the backend moves to
  the next launch level after an allocation failure, and the backend writes the launch files. Only the orchestrator
  sees a failed load: its exit status and the runtime's log, which may echo private input.
- The orchestrator retries a failed load after 5 s and 20 s. A lower level must be in the file before that retry.
- An out of memory during a generation ends the lease with `oom`, but a healthy runtime used to stay loaded, so a new
  level or a tiled image retry would run on the same process and memory.
- The backend had no way to learn that the image runtime would not load, and the table says the job then fails and
  the mode returns to text. `ERROR` was left only through `POST /v1/recover`.

## Decision
- **The orchestrator classifies, the backend lowers.** The supervisor counts an exit with status 3, or a log line that
  matches an allocation pattern, as out of memory. It matches the log and keeps nothing of it. The state document
  carries `load_failure`: the event's `seq`, the mode, the code, `oom`, the failures in a row, `final`, and the job the
  mode was asked for. It stays until that runtime is next ready.
- **The backend lowers the level in two places.** `GpuStateFollower`, which long-polls the state, passes each new state
  to `LaunchLevels.observe`. `JobScheduler` lowers the level after an `LLM_OOM` run, before it releases the lease. The
  rewrite changes only `CONTEXT_SIZE`, `BATCH_SIZE`, `GPU_LAYERS` and `LEVEL`, and adds a comment that names the event
  it answered, so a restarted backend does not answer it again.
- **An `oom` release restarts the runtime.** Recovery stops the runtime of that lease even when it is healthy, verifies
  the release, and the desired mode loads it again with the current launch file.
- **Three failed image loads return to text.** The orchestrator rests in `IDLE`, sets the desired mode to `llm` and
  keeps the failure, with the job's id, in `load_failure`. The scheduler ends that job with `IMAGE_LOAD_FAILED`. The next
  image job asks for the mode again and gets its own three tries.
- **`ERROR` retries on a timer.** Every 300 s, at most three times an hour, with one more load attempt, except for
  `RUNTIME_CONTAINER_MISSING`, which needs `.\setup.cmd` and the user's action.

## Alternatives
- **The orchestrator lowers the level itself.** It would need write access to the data volume, which it does not mount,
  and the backend owns every launch value.
- **The backend reads `GET /v1/events`.** It does not follow the event log yet (P06.5). The state document is already
  long-polled, and a field that stays until the next ready state cannot be missed between two answers.
- **The orchestrator waits for the backend before the retry.** It would need a new call and an answer timeout. The
  follower answers within one long poll, well inside the 5 s delay. A late answer costs one more failed load at the
  same level.
- **A failure that is not out of memory moves the level too.** A missing or corrupt model would then lose context for
  nothing. Those failures retry at the same level.

## Consequences
- The allocation patterns are an assumption until the GPU suite records a real failure. A failure that matches none of
  them is retried at the same level and ends in `ERROR` after three tries.
- A level is only lowered for a catalog profile. A custom profile, a file at level `CUSTOM` or a missing file stays as
  it is. The settings screen that shows the level and returns to A comes with P06.6.
- Every out of memory now costs a runtime restart: a few seconds for ComfyUI, and a model reload for the text runtime.
- Recovery waits for a runtime it finds still starting, up to the load timeout, instead of stopping it.
