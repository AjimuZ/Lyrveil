"""SQL of `scheduled_events` and the reads SchedulerService needs from conversations, messages and memories.

Every function works on a connection the caller owns and never commits. A message counts as sent from the moment its
event is `running` or `delivered`; the event's `updated_at` is that moment, so the counts need no join with `messages`.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, text

from lyrveil.domain.identifiers import from_utc_text, new_id, to_utc_text
from lyrveil.scheduling.rules import Activity, EventKind, MissedPolicy

# The statuses of an event whose message is being written or was written: what the limits count.
_SENT = "('running', 'delivered')"


@dataclass(frozen=True)
class ConversationRow:
    """What the evaluation reads of a conversation with proactive messages switched on."""

    id: str
    character_id: str
    proactive_json: str
    last_message_at: str | None
    created_at: str
    initiative_frequency: str | None
    shares_memory: bool


@dataclass(frozen=True)
class EventRow:
    id: str
    kind: EventKind
    due_at: str
    note: str | None


def enabled_conversations(connection: Connection) -> list[ConversationRow]:
    """Returns the active conversations of active characters whose settings say `enabled`, oldest first. Whether the
    settings are valid is up to the caller: a row with a damaged document is returned and parsed as disabled."""
    rows = connection.execute(
        text(
            "SELECT c.id, c.character_id, c.proactive_json, c.last_message_at, c.created_at, c.shares_memory, "
            "json_extract(ch.communication_json, '$.initiative_frequency') AS initiative_frequency "
            "FROM conversations AS c JOIN characters AS ch ON ch.id = c.character_id "
            "WHERE c.status = 'active' AND c.deleted_at IS NULL AND ch.status = 'active' "
            "AND c.proactive_json IS NOT NULL AND json_extract(c.proactive_json, '$.enabled') = 1 "
            "ORDER BY c.created_at, c.id"
        )
    ).mappings()
    return [
        ConversationRow(
            id=row["id"],
            character_id=row["character_id"],
            proactive_json=row["proactive_json"],
            last_message_at=row["last_message_at"],
            created_at=row["created_at"],
            initiative_frequency=row["initiative_frequency"],
            shares_memory=bool(row["shares_memory"]),
        )
        for row in rows
    ]


def insert_event(
    connection: Connection,
    *,
    conversation_id: str,
    character_id: str,
    kind: EventKind,
    due_at: str,
    note: str | None,
    missed_policy: MissedPolicy,
    created_by: str,
    now: str,
) -> str:
    """Writes a `pending` event and returns its id."""
    event_id = new_id()
    connection.execute(
        text(
            "INSERT INTO scheduled_events (id, conversation_id, character_id, kind, status, due_at, note, "
            "missed_policy, created_by, created_at, updated_at) VALUES (:id, :conversation_id, :character_id, :kind, "
            "'pending', :due_at, :note, :policy, :created_by, :now, :now)"
        ),
        {
            "id": event_id,
            "conversation_id": conversation_id,
            "character_id": character_id,
            "kind": kind.value,
            "due_at": due_at,
            "note": note,
            "policy": missed_policy.value,
            "created_by": created_by,
            "now": now,
        },
    )
    return event_id


def has_event(
    connection: Connection,
    conversation_id: str,
    kind: EventKind,
    *,
    created_since: str | None = None,
    due_at: str | None = None,
    note: str | None = None,
) -> bool:
    """Tells whether the conversation has an event of `kind`, in any status, that was created at or after
    `created_since`, is due at `due_at` and carries `note`; a condition left None is not checked."""
    found = connection.execute(
        text(
            "SELECT 1 FROM scheduled_events WHERE conversation_id = :conversation_id AND kind = :kind "
            "AND (:created_since IS NULL OR created_at >= :created_since) "
            "AND (:due_at IS NULL OR due_at = :due_at) AND (:note IS NULL OR note = :note) LIMIT 1"
        ),
        {
            "conversation_id": conversation_id,
            "kind": kind.value,
            "created_since": created_since,
            "due_at": due_at,
            "note": note,
        },
    ).first()
    return found is not None


def expired_open_threads(connection: Connection, conversation: ConversationRow, since: str, until: str) -> list[str]:
    """Returns the text of the active `open_thread` memories of the conversation, or shared with its character when it
    shares memory, whose `expires_at` (their `valid_until`) fell in `[since, until]`. Oldest first."""
    rows = connection.execute(
        text(
            "SELECT content FROM memories WHERE kind = 'open_thread' AND status = 'active' "
            "AND expires_at IS NOT NULL AND expires_at >= :since AND expires_at <= :until "
            "AND ((scope = 'conversation' AND conversation_id = :conversation_id) "
            "OR (:shares = 1 AND scope = 'shared' AND character_id = :character_id)) "
            "ORDER BY expires_at, id"
        ),
        {
            "since": since,
            "until": until,
            "conversation_id": conversation.id,
            "shares": int(conversation.shares_memory),
            "character_id": conversation.character_id,
        },
    )
    return [row[0] for row in rows]


def due_events(connection: Connection, conversation_id: str, now: str) -> list[EventRow]:
    """Returns the conversation's `pending` events due at or before `now`, oldest first."""
    rows = connection.execute(
        text(
            "SELECT id, kind, due_at, note FROM scheduled_events WHERE conversation_id = :id AND status = 'pending' "
            "AND due_at <= :now ORDER BY due_at, id"
        ),
        {"id": conversation_id, "now": now},
    ).mappings()
    return [EventRow(row["id"], EventKind(row["kind"]), row["due_at"], row["note"]) for row in rows]


@dataclass(frozen=True)
class StaleEvent:
    """A pending event found overdue at startup, with the policy it was created with."""

    id: str
    due_at: str
    missed_policy: MissedPolicy


def stale_pending_events(connection: Connection, conversation_id: str, due_before: str) -> list[StaleEvent]:
    """Returns the conversation's `pending` events whose `due_at` is before `due_before`, oldest first."""
    rows = connection.execute(
        text(
            "SELECT id, due_at, missed_policy FROM scheduled_events WHERE conversation_id = :id "
            "AND status = 'pending' AND due_at < :before ORDER BY due_at, id"
        ),
        {"id": conversation_id, "before": due_before},
    ).mappings()
    return [StaleEvent(row["id"], row["due_at"], MissedPolicy(row["missed_policy"])) for row in rows]


def mark_missed(connection: Connection, event_id: str, reason: str, now: str) -> None:
    """Ends a `pending` event as `missed` with the reason. It never counts against the limits."""
    connection.execute(
        text(
            "UPDATE scheduled_events SET status = 'missed', outcome_reason = :reason, updated_at = :now "
            "WHERE id = :id AND status = 'pending'"
        ),
        {"id": event_id, "reason": reason, "now": now},
    )


def end_interrupted_events(connection: Connection, now: str) -> int:
    """Ends as `failed` (reason `interrupted`) the `running` events that no queued or running job will finish: the job
    row is gone or already ended. Returns how many it changed.

    Without this a pruned job row would leave its event `running` for good, and a `running` event blocks its
    conversation (`has_running_event`). A job that is still queued or running resumes its event, so it is left alone.
    """
    result = connection.execute(
        text(
            "UPDATE scheduled_events SET status = 'failed', outcome_reason = 'interrupted', updated_at = :now "
            "WHERE status = 'running' AND (job_id IS NULL OR job_id NOT IN "
            "(SELECT id FROM generation_jobs WHERE status IN ('queued', 'running')))"
        ),
        {"now": now},
    )
    return int(result.rowcount)


def has_running_event(connection: Connection, conversation_id: str) -> bool:
    """Tells whether a message of the conversation is being decided or written."""
    found = connection.execute(
        text("SELECT 1 FROM scheduled_events WHERE conversation_id = :id AND status = 'running' LIMIT 1"),
        {"id": conversation_id},
    ).first()
    return found is not None


def postpone(connection: Connection, event_id: str, due_at: str, now: str) -> None:
    """Moves a pending event's `due_at`."""
    connection.execute(
        text("UPDATE scheduled_events SET due_at = :due_at, updated_at = :now WHERE id = :id AND status = 'pending'"),
        {"id": event_id, "due_at": due_at, "now": now},
    )


def cancel_pending_events(connection: Connection, conversation_id: str, now: str) -> int:
    """Marks the conversation's `pending` events `cancelled` and returns how many it changed. An event already
    `running` keeps its job: the generation stage ends it."""
    result = connection.execute(
        text(
            "UPDATE scheduled_events SET status = 'cancelled', outcome_reason = 'conversation_deleted', "
            "updated_at = :now WHERE conversation_id = :id AND status = 'pending'"
        ),
        {"id": conversation_id, "now": now},
    )
    return int(result.rowcount)


def start(connection: Connection, event_id: str, job_id: str, now: str) -> None:
    """Marks a pending event `running` with its job. `updated_at` becomes the moment the message counts as sent."""
    connection.execute(
        text(
            "UPDATE scheduled_events SET status = 'running', job_id = :job_id, updated_at = :now "
            "WHERE id = :id AND status = 'pending'"
        ),
        {"id": event_id, "job_id": job_id, "now": now},
    )


@dataclass(frozen=True)
class RunningEvent:
    """An event as the message stages read it: its conversation, kind, note and status."""

    id: str
    conversation_id: str
    kind: EventKind
    note: str | None
    status: str


def get_event(connection: Connection, event_id: str) -> RunningEvent | None:
    """Returns the event with that id in any status, or None."""
    row = (
        connection.execute(
            text("SELECT id, conversation_id, kind, note, status FROM scheduled_events WHERE id = :id"),
            {"id": event_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return RunningEvent(row["id"], row["conversation_id"], EventKind(row["kind"]), row["note"], row["status"])


def finish(
    connection: Connection,
    event_id: str,
    status: str,
    now: str,
    *,
    reason: str | None = None,
    message_id: str | None = None,
) -> bool:
    """Ends a `running` event as `delivered`, `skipped`, `failed` or `cancelled`, with the reason and, when delivered,
    its message. Returns False, changing nothing, when the event is not `running`.

    A `delivered` event keeps its `updated_at`: that is the moment the message began to count against the limits, and
    the limits must not move because the generation took a while.
    """
    result = connection.execute(
        text(
            "UPDATE scheduled_events SET status = :status, outcome_reason = :reason, message_id = :message_id, "
            "updated_at = CASE WHEN :status = 'delivered' THEN updated_at ELSE :now END "
            "WHERE id = :id AND status = 'running'"
        ),
        {"id": event_id, "status": status, "reason": reason, "message_id": message_id, "now": now},
    )
    return bool(result.rowcount)


def recent_proactive_texts(connection: Connection, conversation_id: str, limit: int) -> list[str]:
    """Returns the text of the conversation's last `limit` proactive messages that are not deleted, newest first."""
    rows = connection.execute(
        text(
            "SELECT content FROM messages WHERE conversation_id = :id AND scheduled_event_id IS NOT NULL "
            "AND deleted_at IS NULL AND content != '' ORDER BY seq DESC LIMIT :limit"
        ),
        {"id": conversation_id, "limit": limit},
    )
    return [row[0] for row in rows]


def last_user_message_at(connection: Connection, conversation_id: str) -> datetime | None:
    """Returns the creation time of the user's newest live message in the conversation, or None when there is none."""
    stored = connection.execute(
        text(
            "SELECT max(created_at) FROM messages WHERE conversation_id = :id AND author = 'user' "
            "AND deleted_at IS NULL"
        ),
        {"id": conversation_id},
    ).scalar()
    return None if stored is None else from_utc_text(stored)


def activity(
    connection: Connection, conversation_id: str, day_start: str, day_end: str, last_user_at: datetime | None
) -> Activity:
    """Returns the counts the limits need for the conversation, for the local day `[day_start, day_end)` given as
    stored UTC text. `unanswered` counts the conversation's sent messages after `last_user_at`, all when None."""
    parameters = {
        "id": conversation_id,
        "start": day_start,
        "end": day_end,
        "after": None if last_user_at is None else to_utc_text(last_user_at),
    }
    row = connection.execute(
        text(
            "SELECT max(CASE WHEN conversation_id = :id THEN updated_at END) AS last_sent, "
            "count(CASE WHEN conversation_id = :id AND updated_at >= :start AND updated_at < :end THEN 1 END) "
            "AS today, "
            "count(CASE WHEN updated_at >= :start AND updated_at < :end THEN 1 END) AS today_global, "
            "count(CASE WHEN conversation_id = :id AND (:after IS NULL OR updated_at > :after) THEN 1 END) "
            f"AS unanswered FROM scheduled_events WHERE status IN {_SENT}"
        ),
        parameters,
    ).one()
    return Activity(
        last_sent_at=None if row.last_sent is None else from_utc_text(row.last_sent),
        sent_today=row.today,
        sent_today_global=row.today_global,
        unanswered=row.unanswered,
    )
