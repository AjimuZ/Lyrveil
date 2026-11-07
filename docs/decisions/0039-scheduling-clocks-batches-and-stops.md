# ADR 0039: Scheduling clocks, image batches, preemption and cancellation in the worker

Status: accepted (2026-11-07). Supersedes: none. Refines [ADR 0010](0010-job-queue-and-scheduling-policy.md).

## Context
- [Selection](../gpu-orchestration.md#selection) gives the order of the rules, but not when an image's wait counts,
  what the user's idle time is measured from, or what follows a batch that ends at `images.max_in_a_row`.
- Read literally, the rules switch modes for nothing. An image that waited during a switch to text would take the GPU
  back before the reply that caused the switch runs. After a capped batch, text mode would hand the GPU straight back
  to the next image, because "an image is queued" comes before soft and background work. With only images queued, the
  cap would cost a round trip of about two minutes for no one.
- [Preemption](../gpu-orchestration.md#preemption) and [Cancellation](../gpu-orchestration.md#cancellation) need the
  scheduler to stop a running handler and the runtime's work. Only the handler knows what its job shows the user.
- The scheduler runs one job at a time and already holds the lease of the run it would stop.

## Decision
- **The rules are pure.** `lyrveil/jobs/selection.py` decides from a snapshot of the queue, the batch and the idle
  time. `JobScheduler` keeps the clocks in memory on the monotonic clock, so a restart starts them again.
- **An image waits in text mode only.** Its wait counts from the first pass that sees it in an image stage, and only
  while the text runtime is ready and wanted. Each visit to text mode gives foreground text `images.max_wait_seconds`.
- **The user is idle** from the last pass that saw foreground text queued, or the end of the last foreground run.
- **A batch** starts when the scheduler asks for `image`. It ends at the cap only when soft or background text work
  waits. Until text work runs, or an image takes the GPU back by its wait, text mode does not switch to image for a
  queued image.
- **Stopping a run.** While a GPU stage runs, a watch task checks the job at each `notify()` and every second. A
  cancellation request wins. A background generation is preempted only by a foreground job queued after it started. The
  scheduler sets `RunContext.stop.reason`, cancels the handler, then calls `abort(genkey)` or `interrupt()` under the
  lease it still holds, and stores the job queued again at no attempt cost, or `cancelled`.
- **Cancellation.** `POST /jobs/{id}/cancel` cancels a queued job at once and removes its placeholder in the same
  transaction. A running GPU stage gets `cancel_requested_at` and is stopped by the scheduler. A running stage without
  a GPU is refused with `CONFLICT`: it finishes in a moment. A cancelled reply keeps its text as `interrupted`. Any
  other cancelled job's placeholder is removed by `CancellingHandler.cancelled`.

## Alternatives
- **Apply the rules literally.** Simple to state, but it switches modes for nothing in the cases above.
- **Store the wait start in `generation_jobs`.** It would survive a restart, at the cost of a migration, for a wait of a
  minute that a restart already resets by reloading the model.
- **Preempt for any queued foreground job.** A job left queued on purpose, such as an image after a capped batch, would
  then stop the same background work over and over.
- **Cancel by aborting the runtime and letting the handler finish.** A reply aborted before its first token would run
  its empty-reply retry, and an image prompt would start its next generation.

## Consequences
- The selection is deterministic and unit tested with plain values (T-ORC-12), and the stops are tested through the
  scheduler with fakes (T-IMG-10, T-ORC-12).
- A restart restarts the image waits, the idle time and the batch count.
- A job whose cancellation lands while its run ends in a new stage is ended by the next pass, with its placeholder.
- The drain limit of 120 s for a long foreground reply is still not enforced: the scheduler asks for a mode only
  between jobs, so no image waits on a running reply in the orchestrator.
