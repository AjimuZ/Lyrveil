"""JobQueue: the durable queue in the `generation_jobs` table, with its state changes and the retry policy.

Every method works on a connection the caller owns and never commits, so a job is written in the same transaction as
the change that creates it, and a rollback of that change leaves no job behind. Writers must open that
connection with the WRITE option of lyrveil.persistence.database. Methods that change a running job raise
JobStateError when the job is not running any more, so a stale worker cannot overwrite a newer state.

States (docs/data-model.md): queued -> running -> completed | failed, or back to queued for a retry, a new stage, a
preemption or a recovery after a restart. A queued job can be cancelled at once; a running one gets
`cancel_requested_at`, and the worker that runs it ends it `cancelled`.
"""

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, RowMapping, bindparam, text

from lyrveil.domain.identifiers import from_utc_text, new_id, to_utc_text
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode, policy_for

# ADR 0010: the queue holds at most this many queued and running jobs.
MAX_ACTIVE_JOBS = 200
# error_detail is shown to the user and kept for diagnosis; it never holds private text, and stays short.
MAX_ERROR_DETAIL_CHARS = 500
# The code a job gets when a restart found it running (docs/api.md, Errors).
JOB_INTERRUPTED = "JOB_INTERRUPTED"
# The code of a job the user cancelled (docs/api.md, Errors).
JOB_CANCELLED = "JOB_CANCELLED"
ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)

_COLUMNS = (
    "id, kind, status, stage, required_mode, priority, conversation_id, character_id, message_id, payload_json, "
    "result_json, attempt, max_attempts, not_before, lease_id, genkey, idempotency_key, error_code, error_detail, "
    "cancel_requested_at, created_at, started_at, finished_at"
)


class QueueFullError(Exception):
    """The queue already holds MAX_ACTIVE_JOBS queued and running jobs; nothing was written."""


class JobStateError(Exception):
    """The job does not exist, or is not in the state the change needs. Nothing was written."""


@dataclass(frozen=True)
class Job:
    """One row of `generation_jobs`. `payload` and `result` are the decoded JSON columns; timestamps are aware UTC.

    `attempt` counts the runs started in the current stage: 0 while a new job or a new stage waits, then 1 for the
    first run. `error_code` and `error_detail` hold the last failure, also while a retry waits.
    """

    id: str
    kind: JobKind
    status: JobStatus
    stage: str
    required_mode: RequiredMode
    priority: int
    conversation_id: str | None
    character_id: str | None
    message_id: str | None
    payload: dict[str, Any]
    result: dict[str, Any] | None
    attempt: int
    max_attempts: int
    not_before: datetime | None
    lease_id: str | None
    genkey: str | None
    idempotency_key: str | None
    error_code: str | None
    error_detail: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _time(value: str | None) -> datetime | None:
    return None if value is None else from_utc_text(value)


def _job(row: RowMapping) -> Job:
    return Job(
        id=row["id"],
        kind=JobKind(row["kind"]),
        status=JobStatus(row["status"]),
        stage=row["stage"],
        required_mode=RequiredMode(row["required_mode"]),
        priority=row["priority"],
        conversation_id=row["conversation_id"],
        character_id=row["character_id"],
        message_id=row["message_id"],
        payload=json.loads(row["payload_json"]),
        result=None if row["result_json"] is None else json.loads(row["result_json"]),
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        not_before=_time(row["not_before"]),
        lease_id=row["lease_id"],
        genkey=row["genkey"],
        idempotency_key=row["idempotency_key"],
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        cancel_requested_at=_time(row["cancel_requested_at"]),
        created_at=from_utc_text(row["created_at"]),
        started_at=_time(row["started_at"]),
        finished_at=_time(row["finished_at"]),
    )


def _json(value: Mapping[str, Any] | None) -> str | None:
    return None if value is None else json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JobQueue:
    """Reads and changes jobs. `clock` returns the aware current time; tests pass a fake one."""

    def __init__(self, clock: Callable[[], datetime] = _utc_now, max_active: int = MAX_ACTIVE_JOBS) -> None:
        self._clock = clock
        self._max_active = max_active

    def now(self) -> datetime:
        return self._clock()

    def enqueue(
        self,
        connection: Connection,
        kind: JobKind,
        payload: Mapping[str, Any] | None = None,
        *,
        conversation_id: str | None = None,
        character_id: str | None = None,
        message_id: str | None = None,
        idempotency_key: str | None = None,
        priority: int | None = None,
        stage: str | None = None,
    ) -> Job:
        """Writes a queued job in the caller's transaction and returns it; the caller's commit makes it durable.

        The job starts in the kind's first stage, or in `stage` when given (a regenerated image skips `prompt`), with
        its mode, its maximum attempts and, unless `priority` is given, its default priority. With an
        `idempotency_key` already stored, returns that job and writes nothing. Raises QueueFullError when the queue
        holds MAX_ACTIVE_JOBS active jobs, and ValueError for a `stage` the kind does not have.
        """
        if idempotency_key is not None:
            existing = connection.execute(
                text(f"SELECT {_COLUMNS} FROM generation_jobs WHERE idempotency_key = :key"), {"key": idempotency_key}
            ).one_or_none()
            if existing is not None:
                return _job(existing._mapping)
        active = self.depth(connection)
        if active >= self._max_active:
            raise QueueFullError(f"the queue already holds {active} active jobs")
        policy = policy_for(kind)
        stage = policy.first_stage if stage is None else stage
        if stage not in policy.stages:
            raise ValueError(f"{kind.value} has no stage {stage!r}")
        job_id = new_id()
        connection.execute(
            text(
                "INSERT INTO generation_jobs (id, kind, status, stage, required_mode, priority, conversation_id, "
                "character_id, message_id, payload_json, attempt, max_attempts, idempotency_key, created_at) VALUES "
                "(:id, :kind, 'queued', :stage, :mode, :priority, :conversation_id, :character_id, :message_id, "
                ":payload, 0, :max_attempts, :key, :now)"
            ),
            {
                "id": job_id,
                "kind": kind.value,
                "stage": stage,
                "mode": policy.required_mode(stage).value,
                "priority": policy.priority if priority is None else priority,
                "conversation_id": conversation_id,
                "character_id": character_id,
                "message_id": message_id,
                "payload": _json(payload or {}),
                "max_attempts": policy.max_attempts,
                "key": idempotency_key,
                "now": to_utc_text(self.now()),
            },
        )
        return self._get(connection, job_id)

    def depth(self, connection: Connection) -> int:
        """Returns the number of active jobs, queued or running: the queue depth that `gpu.state` reports and that
        MAX_ACTIVE_JOBS bounds."""
        active: int = connection.execute(
            text("SELECT count(*) FROM generation_jobs WHERE status IN ('queued', 'running')")
        ).scalar_one()
        return active

    def has_queued(
        self, connection: Connection, kind: JobKind, conversation_id: str, payload: Mapping[str, Any]
    ) -> bool:
        """Tells whether a job of `kind` for the conversation waits `queued` with this payload, compared as the JSON
        `enqueue` stores (so with the same key order). A running job does not count: it may have read its input
        before the change that asks again."""
        found = connection.execute(
            text(
                "SELECT 1 FROM generation_jobs WHERE kind = :kind AND conversation_id = :id AND status = 'queued' "
                "AND payload_json = :payload LIMIT 1"
            ),
            {"kind": kind.value, "id": conversation_id, "payload": _json(payload)},
        ).first()
        return found is not None

    def get(self, connection: Connection, job_id: str) -> Job | None:
        """Returns the job, or None when no job has that id."""
        row = connection.execute(
            text(f"SELECT {_COLUMNS} FROM generation_jobs WHERE id = :id"), {"id": job_id}
        ).one_or_none()
        return None if row is None else _job(row._mapping)

    def next_eligible(
        self, connection: Connection, modes: Collection[RequiredMode], kinds: Collection[JobKind]
    ) -> Job | None:
        """Returns the queued job that should run next among those whose stage needs one of `modes` and whose kind is
        in `kinds`, or None. A job waiting for its backoff, or with a cancellation requested, is not eligible.

        Order: lower priority first, then the oldest, then the id, so the choice is deterministic.
        """
        found = self._eligible(connection, modes, kinds, limit=1)
        return found[0] if found else None

    def eligible(
        self, connection: Connection, modes: Collection[RequiredMode], kinds: Collection[JobKind]
    ) -> Sequence[Job]:
        """Returns every job `next_eligible` chooses among, in the same order. MAX_ACTIVE_JOBS bounds the list."""
        return self._eligible(connection, modes, kinds, limit=None)

    def next_cancel_pending(self, connection: Connection, kinds: Collection[JobKind]) -> Job | None:
        """Returns the oldest queued job of `kinds` whose cancellation was requested while it ran, or None. Such a job
        is left behind by a restart, or by a run that ended in a new stage just as the request came; the worker ends
        it with `cancel_queued`."""
        if not kinds:
            return None
        statement = text(
            f"SELECT {_COLUMNS} FROM generation_jobs WHERE status = 'queued' AND cancel_requested_at IS NOT NULL "
            "AND kind IN :kinds ORDER BY cancel_requested_at, id LIMIT 1"
        ).bindparams(bindparam("kinds", expanding=True))
        row = connection.execute(statement, {"kinds": sorted(kind.value for kind in kinds)}).one_or_none()
        return None if row is None else _job(row._mapping)

    def _eligible(
        self,
        connection: Connection,
        modes: Collection[RequiredMode],
        kinds: Collection[JobKind],
        *,
        limit: int | None,
    ) -> Sequence[Job]:
        if not modes or not kinds:
            return []
        statement = text(
            f"SELECT {_COLUMNS} FROM generation_jobs WHERE status = 'queued' AND cancel_requested_at IS NULL "
            "AND required_mode IN :modes AND kind IN :kinds AND (not_before IS NULL OR not_before <= :now) "
            "ORDER BY priority, created_at, id" + ("" if limit is None else f" LIMIT {int(limit)}")
        ).bindparams(bindparam("modes", expanding=True), bindparam("kinds", expanding=True))
        rows = connection.execute(
            statement,
            {
                "modes": sorted(mode.value for mode in modes),
                "kinds": sorted(kind.value for kind in kinds),
                "now": to_utc_text(self.now()),
            },
        ).all()
        return [_job(row._mapping) for row in rows]

    def start(self, connection: Connection, job_id: str, lease_id: str | None, genkey: str) -> Job | None:
        """Marks a queued job running, counts the attempt and stores its lease and abort key; returns the job.

        Returns None, and writes nothing, when the job is no longer queued or a cancellation was requested.
        """
        updated = connection.execute(
            text(
                "UPDATE generation_jobs SET status = 'running', attempt = attempt + 1, lease_id = :lease, "
                "genkey = :genkey, started_at = :now, not_before = NULL "
                "WHERE id = :id AND status = 'queued' AND cancel_requested_at IS NULL"
            ),
            {"id": job_id, "lease": lease_id, "genkey": genkey, "now": to_utc_text(self.now())},
        )
        return self._get(connection, job_id) if updated.rowcount == 1 else None

    def complete(self, connection: Connection, job_id: str, result: Mapping[str, Any] | None = None) -> Job:
        """Ends a running job as completed with `result`. Raises JobStateError when it is not running."""
        return self._change_running(
            connection,
            job_id,
            "status = 'completed', result_json = :result, finished_at = :now, lease_id = NULL, "
            "error_code = NULL, error_detail = NULL",
            {"result": _json(result)},
        )

    def advance(self, connection: Connection, job_id: str, stage: str, payload: Mapping[str, Any] | None = None) -> Job:
        """Queues a running job again in `stage`, with that stage's mode and a fresh attempt count, so each stage has
        the kind's full attempts. `payload`, when given, replaces the stored one. Raises ValueError for a stage the
        kind does not have, and JobStateError when the job is not running.
        """
        job = self._running(connection, job_id)
        policy = policy_for(job.kind)
        if stage not in policy.stages:
            raise ValueError(f"{job.kind.value} has no stage {stage!r}")
        return self._change_running(
            connection,
            job_id,
            "status = 'queued', stage = :stage, required_mode = :mode, attempt = 0, lease_id = NULL, "
            "payload_json = coalesce(:payload, payload_json), error_code = NULL, error_detail = NULL",
            {"stage": stage, "mode": policy.required_mode(stage).value, "payload": _json(payload)},
        )

    def fail(
        self,
        connection: Connection,
        job_id: str,
        code: str,
        detail: str | None,
        *,
        retryable: bool,
        fallback: Mapping[str, Any] | None = None,
    ) -> Job:
        """Records a failed attempt of a running job and returns the job.

        When `retryable` and attempts remain, the job is queued again after the kind's backoff for this attempt;
        otherwise it ends as failed. A retryable failure with a `fallback` payload is queued again in its stage with
        that payload and the attempt count reset to 0, after the same backoff, whatever attempts remain
        (JobFailedError.fallback). Either way `error_code` and `error_detail` (cut to MAX_ERROR_DETAIL_CHARS) hold the
        reason. Raises JobStateError when the job is not running.
        """
        job = self._running(connection, job_id)
        reason = {"code": code, "detail": None if detail is None else detail[:MAX_ERROR_DETAIL_CHARS]}
        if retryable and fallback is not None:
            delay = timedelta(seconds=policy_for(job.kind).retry_delay_s(job.attempt))
            return self._change_running(
                connection,
                job_id,
                "status = 'queued', not_before = :not_before, lease_id = NULL, attempt = 0, "
                "payload_json = :payload, error_code = :code, error_detail = :detail",
                {**reason, "not_before": to_utc_text(self.now() + delay), "payload": _json(fallback)},
            )
        if retryable and job.attempt < job.max_attempts:
            delay = timedelta(seconds=policy_for(job.kind).retry_delay_s(job.attempt))
            return self._change_running(
                connection,
                job_id,
                "status = 'queued', not_before = :not_before, lease_id = NULL, error_code = :code, "
                "error_detail = :detail",
                {**reason, "not_before": to_utc_text(self.now() + delay)},
            )
        return self._change_running(
            connection,
            job_id,
            "status = 'failed', finished_at = :now, lease_id = NULL, error_code = :code, error_detail = :detail",
            reason,
        )

    def requeue_preempted(self, connection: Connection, job_id: str) -> Job:
        """Queues a running job again at once and gives its attempt back: preemption costs no attempt
        (docs/backend.md, Retry policy). Raises JobStateError when the job is not running."""
        return self._change_running(
            connection,
            job_id,
            "status = 'queued', attempt = max(attempt - 1, 0), lease_id = NULL, not_before = NULL",
            {},
        )

    def cancel_queued(self, connection: Connection, job_id: str) -> Job | None:
        """Ends a queued job as `cancelled` with JOB_CANCELLED, and returns it; None, writing nothing, when the job is
        not queued. `cancel_requested_at` keeps the first request's time."""
        now = to_utc_text(self.now())
        updated = connection.execute(
            text(
                "UPDATE generation_jobs SET status = 'cancelled', cancel_requested_at = coalesce(cancel_requested_at, "
                ":now), finished_at = :now, not_before = NULL, lease_id = NULL, error_code = :code, "
                "error_detail = NULL WHERE id = :id AND status = 'queued'"
            ),
            {"id": job_id, "now": now, "code": JOB_CANCELLED},
        )
        return self._get(connection, job_id) if updated.rowcount == 1 else None

    def request_cancel(self, connection: Connection, job_id: str) -> Job | None:
        """Records that the user cancelled a running job, and returns it; None, writing nothing, when the job is not
        running. A repeated request keeps the first time. The job stays running until its worker stops it and calls
        `end_cancelled`; until then `start` and `next_eligible` skip it if it is queued again."""
        updated = connection.execute(
            text(
                "UPDATE generation_jobs SET cancel_requested_at = coalesce(cancel_requested_at, :now) "
                "WHERE id = :id AND status = 'running'"
            ),
            {"id": job_id, "now": to_utc_text(self.now())},
        )
        return self._get(connection, job_id) if updated.rowcount == 1 else None

    def end_cancelled(self, connection: Connection, job_id: str) -> Job:
        """Ends a running job as `cancelled` with JOB_CANCELLED. Raises JobStateError when it is not running."""
        return self._change_running(
            connection,
            job_id,
            "status = 'cancelled', cancel_requested_at = coalesce(cancel_requested_at, :now), finished_at = :now, "
            "lease_id = NULL, not_before = NULL, error_code = :code, error_detail = NULL",
            {"code": JOB_CANCELLED},
        )

    def recover_interrupted(self, connection: Connection) -> Sequence[Job]:
        """Handles the jobs a restart found running, and returns them in their new state.

        Each one is queued again at once in the stage it was running, because a stage writes its results only when it
        ends. The interrupted run counts as an attempt, so a job that brings the process down cannot loop forever:
        one that was on its last attempt ends as failed. Both keep JOB_INTERRUPTED as the reason, and the lease,
        which expires by itself, is cleared. Requires that no worker runs yet.
        """
        rows = connection.execute(
            text("SELECT id, attempt, max_attempts FROM generation_jobs WHERE status = 'running' ORDER BY id")
        ).all()
        now = to_utc_text(self.now())
        for job_id, attempt, max_attempts in rows:
            status = JobStatus.QUEUED if attempt < max_attempts else JobStatus.FAILED
            connection.execute(
                text(
                    "UPDATE generation_jobs SET status = :status, lease_id = NULL, not_before = NULL, "
                    "error_code = :code, error_detail = NULL, "
                    "finished_at = CASE WHEN :status = 'failed' THEN :now ELSE NULL END WHERE id = :id"
                ),
                {"id": job_id, "status": status.value, "code": JOB_INTERRUPTED, "now": now},
            )
        return [self._get(connection, job_id) for job_id, _, _ in rows]

    def _get(self, connection: Connection, job_id: str) -> Job:
        job = self.get(connection, job_id)
        if job is None:
            raise JobStateError(f"job {job_id} does not exist")
        return job

    def _running(self, connection: Connection, job_id: str) -> Job:
        job = self._get(connection, job_id)
        if job.status is not JobStatus.RUNNING:
            raise JobStateError(f"job {job_id} is {job.status.value}, not running")
        return job

    def _change_running(self, connection: Connection, job_id: str, assignments: str, params: dict[str, Any]) -> Job:
        values = {**params, "id": job_id}
        if ":now" in assignments:
            values["now"] = to_utc_text(self.now())
        updated = connection.execute(
            text(f"UPDATE generation_jobs SET {assignments} WHERE id = :id AND status = 'running'"), values
        )
        if updated.rowcount != 1:
            raise JobStateError(f"job {job_id} is not running")
        return self._get(connection, job_id)
