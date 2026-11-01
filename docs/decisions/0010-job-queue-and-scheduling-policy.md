# ADR 0010: Durable SQLite job queue with mode batching

Status: accepted (2026-11-01). Supersedes: none.

## Context
Jobs must survive restarts, support priorities, cancellation and limited retries, and must not need Redis. A mode switch costs a minute or more. Users wait on replies, while extraction and summaries can wait. Images must not starve when the user keeps chatting.

## Decision
- `generation_jobs` is the queue. One worker in the backend process selects jobs.
- Each job has a kind, a stage and a `required_mode` derived from the stage.
- Three classes: foreground, soft and background. Soft and background work never causes a mode change, and runs only when the user is idle.
- In text mode, foreground text runs first, unless an image has waited 60 s. In image mode, foreground text wins as soon as the running image ends. Consecutive images are limited to 4.
- A foreground job preempts background generation with `POST /api/extra/abort`, at no attempt cost.
- The queue caps at 200 active jobs.

## Alternatives
- Celery, RQ or Redis: an extra service for one worker on one machine, which the requirements exclude without a concrete need.
- First in, first out: simple, but it causes needless switches and hides replies behind maintenance work.
- Strict priority with no image limit: images could starve.

## Consequences
Replies stay responsive and image work makes progress. Behavior is deterministic and testable with a fake clock (T-ORC-12). Values are settings, so they can be tuned after measuring real switch times. One worker means no parallel GPU work, which matches the hardware.
