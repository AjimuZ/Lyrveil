# ADR 0038: The orchestrator holds a load back for VRAM, the backend ends the job

Status: accepted (2026-11-07). Supersedes: none.

## Context
- [GPU orchestration](../gpu-orchestration.md#verifying-gpu-release) asks for a VRAM check before each load. When free
  VRAM is below the requirement, the state stays `IDLE` with `blocked_reason = GPU_INSUFFICIENT_VRAM` and the check
  repeats every 5 s. After `vram.wait_seconds` (120) the waiting job fails with a message that names the numbers, the
  next job can try, and external processes are never terminated.
- The orchestrator reads the card, but it knows jobs only through the `job_id` of a mode request. A text job that
  waits for the default mode `llm` sends no request, so the orchestrator cannot tell which job is waiting.
- The backend owns the jobs, the messages they show and the application settings, `vram.wait_seconds` among them.
- `seq` in the state document moved only with a transition. The table has no transition from `IDLE` to itself, so the
  start of a hold would not wake the long poll that publishes `gpu.state`.
- No path ended a queued job outside its handler, and only the handler knows the message or variant to mark failed.

## Decision
- **The orchestrator holds and reports.** Before each load from `IDLE`, `ProcessSupervisor.insufficient_vram` reads
  NVML. Free memory below the requirement, with neither runtime container running, holds the load. The hold is
  `blocked_reason` and `blocked_vram` (`mode`, `free_mib`, `required_mib`) in the state document, and the check
  repeats every 5 s for as long as the desired mode needs a runtime. A running runtime container means the memory may
  be Lyrveil's own, so the start goes ahead and meets Exclusivity rule 3 or recovery. An NVML or Docker failure does not
  hold the load either: the start reads them again and fails on its own. The requirements are 6000 MiB for text, the
  smallest of the catalog, and 6500 MiB for images, until the backend sends the active profile's value (P06.6).
- **A hold is an event.** Its start, and its end without a load, are recorded with `from` and `to` both `IDLE`, so
  `seq` moves. A hold that ends in a load ends with that load's transition.
- **The backend times the wait.** `JobScheduler` gives the job that decides the mode `vram.wait_seconds` from the first
  pass that sees the hold for it. Then the job fails with `GPU_INSUFFICIENT_VRAM`, not retried, and a detail with both
  numbers. The next job gets a wait of its own.
- **Handlers record a failure before the stage.** The scheduler marks the job running without a lease, calls
  `AbandoningHandler.abandon` when the handler has it, and stores the failure as for a run. The reply and image
  handlers set their message `failed` and publish `message.failed`. `abandon` never touches a runtime.

## Alternatives
- **The orchestrator times the wait and names the job.** It would need every waiting job to send a mode request, and a
  new state field for an expired wait. The settings would also live in two places.
- **Block when NVML fails.** The message would then tell the user to close other programs for a failure that is not
  theirs.
- **Count memory of a running runtime as external.** A runtime started by hand would then hold the GPU in `IDLE`
  forever, and recovery would never see it.
- **Fail the queued job in the queue only.** The reply or the image placeholder would wait forever in the conversation.

## Consequences
- `GET /v1/events` can return events with `from` equal to `to`. Their reason and `code` tell them apart from
  transitions.
- `vram.wait_seconds` is the default 120 until the backend reads `application_settings` for it. The text requirement
  does not follow the active profile until P06.6.
- The **Risorse AI** screen and the conversation show the hold only through `gpu.state` and the failed message, until
  P06.6 adds the screen.
