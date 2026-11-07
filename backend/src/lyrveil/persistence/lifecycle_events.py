"""SQL of the `model_lifecycle_events` table: the orchestrator's events as the backend stores them (docs/data-model.md).

Every function works on a connection the caller owns and never commits. Times are the UTC text of
lyrveil.domain.identifiers, so text comparison orders them. `(boot_id, seq)` identifies an event: storing it again
changes nothing.
"""

from dataclasses import dataclass

from sqlalchemy import Connection, text

from lyrveil.domain.identifiers import new_id

# docs/security.md (Retention): the newest 50,000 rows are kept.
KEEP_EVENTS = 50_000


@dataclass(frozen=True)
class EventRow:
    """One event to store. `detail_json` is the event's `details` as JSON; `vram_used_mib` and `vram_total_mib` are
    None when the event does not report them."""

    boot_id: str
    seq: int
    from_state: str
    to_state: str
    reason: str
    code: str | None
    job_id: str | None
    vram_total_mib: int | None
    vram_used_mib: int | None
    detail_json: str
    occurred_at: str


@dataclass(frozen=True)
class Cursor:
    """Where reading the orchestrator's events resumes: the newest stored event's `boot_id`, and the highest `seq`
    stored for that boot."""

    boot_id: str
    seq: int


def insert(connection: Connection, row: EventRow) -> bool:
    """Stores `row` and returns True, or returns False and writes nothing when an event with its `boot_id` and `seq` is
    already stored."""
    result = connection.execute(
        text(
            "INSERT INTO model_lifecycle_events (id, boot_id, seq, from_state, to_state, reason, code, job_id, "
            "vram_total_mib, vram_used_mib, detail_json, occurred_at) VALUES (:id, :boot_id, :seq, :from_state, "
            ":to_state, :reason, :code, :job_id, :vram_total_mib, :vram_used_mib, :detail_json, :occurred_at) "
            "ON CONFLICT (boot_id, seq) DO NOTHING"
        ),
        {
            "id": new_id(),
            "boot_id": row.boot_id,
            "seq": row.seq,
            "from_state": row.from_state,
            "to_state": row.to_state,
            "reason": row.reason,
            "code": row.code,
            "job_id": row.job_id,
            "vram_total_mib": row.vram_total_mib,
            "vram_used_mib": row.vram_used_mib,
            "detail_json": row.detail_json,
            "occurred_at": row.occurred_at,
        },
    )
    return result.rowcount == 1


def cursor(connection: Connection) -> Cursor | None:
    """Returns where reading resumes, or None when nothing is stored.

    The newest event is the one with the latest `occurred_at`, then the highest `seq`. The orchestrator's clock is the
    host's, so a later process writes later times; only a clock set back between two orchestrator processes could
    make the older process look newer, and then its buffer is simply read again, which stores nothing twice.
    """
    newest = connection.execute(
        text("SELECT boot_id FROM model_lifecycle_events ORDER BY occurred_at DESC, seq DESC LIMIT 1")
    ).scalar()
    if newest is None:
        return None
    seq: int = connection.execute(
        text("SELECT max(seq) FROM model_lifecycle_events WHERE boot_id = :boot_id"), {"boot_id": newest}
    ).scalar_one()
    return Cursor(boot_id=newest, seq=seq)


def prune(connection: Connection, keep: int = KEEP_EVENTS) -> int:
    """Deletes every event but the newest `keep`, by `occurred_at` then `seq`, and returns how many it deleted.
    Requires keep >= 1."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    result = connection.execute(
        text(
            "DELETE FROM model_lifecycle_events WHERE id IN (SELECT id FROM model_lifecycle_events "
            "ORDER BY occurred_at DESC, seq DESC LIMIT -1 OFFSET :keep)"
        ),
        {"keep": keep},
    )
    return result.rowcount
