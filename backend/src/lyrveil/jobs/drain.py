"""The queue drain before a shutdown: step 1 of docs/gpu-orchestration.md (Safe shutdown).

`stop.cmd` runs `python -m lyrveil.cli drain --timeout 60` in the backend container. That command is a separate process
from the server, so the pause travels through the database (JobQueue.set_paused). The scheduler starts nothing while
the queue is paused, and JobQueue.start refuses in the same transaction that would mark a job running, so no job can
start after the drain has seen the queue empty. A job already running is not stopped: the drain waits for it to end,
for up to the timeout. The backend resumes the queue at every start, so a stop followed by a start needs nothing
else; `python -m lyrveil.cli resume` resumes it after a stop that did not happen.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import read, write

# How often the drain looks at the running jobs while it waits.
DRAIN_POLL_S = 0.5


@dataclass(frozen=True)
class DrainResult:
    """What a drain left: `running`, the jobs still running when it returned, 0 once the queue is drained, and
    `waited_s`, how long it waited for them."""

    running: int
    waited_s: float

    @property
    def drained(self) -> bool:
        return self.running == 0


def pause_queue(engine: Engine, queue: JobQueue) -> None:
    """Pauses the queue in one committed transaction: from then on no job starts."""
    write(engine, lambda connection: queue.set_paused(connection, True))


def resume_queue(engine: Engine, queue: JobQueue) -> None:
    """Resumes the queue in one committed transaction: jobs start again at the scheduler's next pass."""
    write(engine, lambda connection: queue.set_paused(connection, False))


def drain_queue(
    engine: Engine,
    queue: JobQueue,
    timeout_s: float,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = DRAIN_POLL_S,
) -> DrainResult:
    """Pauses the queue, then waits until no job runs or `timeout_s` has passed, and returns what is left.

    The queue stays paused either way. A job still running at the timeout goes on until the backend stops, and the
    next start recovers it: a reply becomes `interrupted`, any other job is queued again. Blocks. Raises ValueError for
    a negative `timeout_s`.
    """
    if timeout_s < 0:
        raise ValueError("timeout_s must be at least 0")
    pause_queue(engine, queue)
    started = monotonic()
    while True:
        running = read(engine, queue.running_count)
        waited = monotonic() - started
        if running == 0 or waited >= timeout_s:
            return DrainResult(running=running, waited_s=waited)
        sleep(min(poll_s, timeout_s - waited))
