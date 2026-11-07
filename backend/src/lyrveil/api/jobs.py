"""Job routes of docs/api.md (Jobs): `GET /jobs/{id}`, the state and result of one job, and the worker wake-up that
routes queuing a job share.

The list, cancel and retry routes of that section are planned. Not in the committed OpenAPI document yet, for the
reason given in lyrveil.api.conversations.
"""

from typing import Any

import anyio.from_thread
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import Connection, Engine

from lyrveil.domain.errors import NotFoundError
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import read
from lyrveil.persistence import messages as message_repository

router = APIRouter(prefix="/jobs", tags=["jobs"], include_in_schema=False)

_NO_JOB = "Operazione non trovata."


class JobOut(BaseModel):
    """A job as a route that queued it answers. `queue_position` 1 means it runs next."""

    id: str
    kind: str
    status: str
    queue_position: int


class JobDetail(BaseModel):
    """One job without its payload, which can hold private text. `result` is the stored `result_json`, null until the
    job completes; `error_code` and `error_detail` hold the last failure, also while a retry waits."""

    id: str
    kind: str
    status: str
    stage: str
    attempt: int
    max_attempts: int
    queue_position: int
    error_code: str | None
    error_detail: str | None
    result: dict[str, Any] | None
    created_at: str
    finished_at: str | None


def wake_worker(request: Request) -> None:
    """Lets the job worker pick the job just queued at once instead of at its next poll. Needs the route's worker
    thread, which FastAPI gives every plain `def` route. Does nothing while no worker runs."""
    scheduler = request.app.state.scheduler
    if scheduler is not None:
        anyio.from_thread.run_sync(scheduler.notify)


def _detail(job: Job, position: int) -> JobDetail:
    return JobDetail(
        id=job.id,
        kind=job.kind.value,
        status=job.status.value,
        stage=job.stage,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        queue_position=position,
        error_code=job.error_code,
        error_detail=job.error_detail,
        result=job.result,
        created_at=to_utc_text(job.created_at),
        finished_at=None if job.finished_at is None else to_utc_text(job.finished_at),
    )


@router.get("/{job_id}", response_model=JobDetail)
def read_job(job_id: str, request: Request) -> JobDetail:
    """Returns the job's state, its place in the queue (0 once it ended) and its result. Answers 404 `NOT_FOUND` when
    no job has that id."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the job routes need a database")
    queue = JobQueue()

    def work(connection: Connection) -> JobDetail | None:
        job = queue.get(connection, job_id)
        return None if job is None else _detail(job, message_repository.queue_position(connection, job.id))

    found = read(engine, work)
    if found is None:
        raise NotFoundError(_NO_JOB)
    return found
