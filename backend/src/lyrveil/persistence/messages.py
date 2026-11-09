"""SQL of the `messages` and `message_variants` tables, and the conversation counters a new message moves.

Every function works on a connection the caller owns and never commits. Writers open that connection with the WRITE
option of lyrveil.persistence.database, so the service decides what one transaction holds.
"""

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.messages import Author, Message, MessageStatus, Variant, VariantSource, VariantStatus

_MESSAGE_SELECT = """
SELECT m.id, m.conversation_id, m.seq, m.client_message_id, m.author, m.kind, m.status, m.read_state,
       m.active_variant_id, m.content, m.edited_at, m.deleted_at, m.created_at,
       (SELECT count(*) FROM message_variants AS v WHERE v.message_id = m.id) AS variant_count,
       (SELECT v.image_id FROM message_variants AS v JOIN generated_images AS g ON g.id = v.image_id
         WHERE v.id = m.active_variant_id AND g.deleted_at IS NULL) AS image_id
FROM messages AS m
"""
_VARIANT_SELECT = "SELECT id, message_id, variant_no, content, source, status, created_at FROM message_variants"


def _message(row: RowMapping) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        seq=row["seq"],
        client_message_id=row["client_message_id"],
        author=Author(row["author"]),
        kind=row["kind"],
        status=MessageStatus(row["status"]),
        read_state=row["read_state"],
        active_variant_id=row["active_variant_id"],
        content=row["content"],
        variant_count=row["variant_count"],
        edited_at=row["edited_at"],
        deleted_at=row["deleted_at"],
        created_at=row["created_at"],
        image_id=row["image_id"],
    )


def _variant(row: RowMapping) -> Variant:
    return Variant(
        id=row["id"],
        message_id=row["message_id"],
        variant_no=row["variant_no"],
        content=row["content"],
        source=VariantSource(row["source"]),
        status=VariantStatus(row["status"]),
        created_at=row["created_at"],
    )


def take_seq(connection: Connection, conversation_id: str, now: str) -> int:
    """Returns the conversation's next message number and moves its counter and `last_message_at` past it.

    Requires a write transaction, so two senders never get the same number. The conversation must exist.
    """
    seq: int = connection.execute(
        text("SELECT next_seq FROM conversations WHERE id = :id"), {"id": conversation_id}
    ).scalar_one()
    connection.execute(
        text("UPDATE conversations SET next_seq = :next, last_message_at = :now, updated_at = :now WHERE id = :id"),
        {"next": seq + 1, "now": now, "id": conversation_id},
    )
    return seq


def insert_message(
    connection: Connection,
    *,
    message_id: str,
    conversation_id: str,
    seq: int,
    client_message_id: str | None,
    author: Author,
    status: MessageStatus,
    read_state: str | None,
    content: str,
    now: str,
    scheduled_event_id: str | None = None,
) -> None:
    """Adds a text message with no active variant yet. `scheduled_event_id` marks a proactive message."""
    connection.execute(
        text(
            "INSERT INTO messages (id, conversation_id, seq, client_message_id, author, kind, status, read_state, "
            "content, scheduled_event_id, created_at) VALUES (:id, :conversation_id, :seq, :client_message_id, "
            ":author, 'text', :status, :read_state, :content, :scheduled_event_id, :now)"
        ),
        {
            "id": message_id,
            "conversation_id": conversation_id,
            "seq": seq,
            "client_message_id": client_message_id,
            "author": author.value,
            "status": status.value,
            "read_state": read_state,
            "content": content,
            "scheduled_event_id": scheduled_event_id,
            "now": now,
        },
    )


def insert_variant(
    connection: Connection,
    *,
    variant_id: str,
    message_id: str,
    content: str,
    content_hash: str,
    source: VariantSource,
    status: VariantStatus,
    now: str,
) -> int:
    """Adds a variant numbered after the message's last one and returns its number."""
    variant_no: int = connection.execute(
        text("SELECT coalesce(max(variant_no), 0) + 1 FROM message_variants WHERE message_id = :id"),
        {"id": message_id},
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO message_variants (id, message_id, variant_no, content, content_hash, source, status, "
            "created_at) VALUES (:id, :message_id, :variant_no, :content, :content_hash, :source, :status, :now)"
        ),
        {
            "id": variant_id,
            "message_id": message_id,
            "variant_no": variant_no,
            "content": content,
            "content_hash": content_hash,
            "source": source.value,
            "status": status.value,
            "now": now,
        },
    )
    return variant_no


def update_message(connection: Connection, message_id: str, **columns: object) -> None:
    """Sets the given columns of the message. Column names come from the service, never from a request."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(text(f"UPDATE messages SET {assignments} WHERE id = :id"), {**columns, "id": message_id})


def update_variant(connection: Connection, variant_id: str, **columns: object) -> None:
    """Sets the given columns of the variant. Column names come from the service, never from a request."""
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    connection.execute(text(f"UPDATE message_variants SET {assignments} WHERE id = :id"), {**columns, "id": variant_id})


def message_rows(connection: Connection, clause: str, params: dict[str, object]) -> list[Message]:
    """Returns the messages selected by the SQL `clause` (WHERE, ORDER BY and LIMIT over alias `m`) with `params`.
    The clause comes from persistence code, never from a request."""
    rows = connection.execute(text(f"{_MESSAGE_SELECT} {clause}"), params).mappings().all()
    return [_message(row) for row in rows]


def get_message(connection: Connection, message_id: str) -> Message | None:
    """Returns the message in any state, deleted included, or None when no row has that id."""
    row = connection.execute(text(f"{_MESSAGE_SELECT} WHERE m.id = :id"), {"id": message_id}).mappings().first()
    return None if row is None else _message(row)


def find_by_client_id(connection: Connection, conversation_id: str, client_message_id: str) -> Message | None:
    """Returns the message the conversation stored for `client_message_id`, or None."""
    row = (
        connection.execute(
            text(f"{_MESSAGE_SELECT} WHERE m.conversation_id = :conversation_id AND m.client_message_id = :client_id"),
            {"conversation_id": conversation_id, "client_id": client_message_id},
        )
        .mappings()
        .first()
    )
    return None if row is None else _message(row)


def find_by_seq(connection: Connection, conversation_id: str, seq: int) -> Message | None:
    """Returns the message with that number in the conversation, deleted included, or None."""
    row = (
        connection.execute(
            text(f"{_MESSAGE_SELECT} WHERE m.conversation_id = :conversation_id AND m.seq = :seq"),
            {"conversation_id": conversation_id, "seq": seq},
        )
        .mappings()
        .first()
    )
    return None if row is None else _message(row)


def last_visible(connection: Connection, conversation_id: str) -> Message | None:
    """Returns the newest message that is not deleted, or None when the conversation has none."""
    row = (
        connection.execute(
            text(
                f"{_MESSAGE_SELECT} WHERE m.conversation_id = :id AND m.deleted_at IS NULL ORDER BY m.seq DESC LIMIT 1"
            ),
            {"id": conversation_id},
        )
        .mappings()
        .first()
    )
    return None if row is None else _message(row)


def page(connection: Connection, conversation_id: str, before_seq: int | None, limit: int) -> list[Message]:
    """Returns up to `limit` messages that are not deleted and have `seq` below `before_seq` (all when None), newest
    first."""
    clause = "" if before_seq is None else "AND m.seq < :before_seq"
    rows = (
        connection.execute(
            text(
                f"{_MESSAGE_SELECT} WHERE m.conversation_id = :id AND m.deleted_at IS NULL {clause} "
                "ORDER BY m.seq DESC LIMIT :limit"
            ),
            {"id": conversation_id, "before_seq": before_seq, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_message(row) for row in rows]


def prompt_messages(connection: Connection, conversation_id: str, before_seq: int, limit: int) -> list[Message]:
    """Returns up to `limit` of the newest messages below `before_seq` that a prompt may read, oldest first: not
    deleted, with text, and `complete` or `interrupted`, so a failed or unfinished reply never enters a prompt."""
    rows = (
        connection.execute(
            text(
                f"{_MESSAGE_SELECT} WHERE m.conversation_id = :id AND m.deleted_at IS NULL AND m.seq < :before_seq "
                "AND m.content != '' AND m.status IN ('complete', 'interrupted') ORDER BY m.seq DESC LIMIT :limit"
            ),
            {"id": conversation_id, "before_seq": before_seq, "limit": limit},
        )
        .mappings()
        .all()
    )
    return [_message(row) for row in reversed(rows)]


def reply_checks(connection: Connection, conversation_id: str, before_seq: int, limit: int) -> list[str | None]:
    """Returns the `checks_json` of the active variants of up to `limit` of the newest character replies below
    `before_seq` that a prompt may read (as `prompt_messages` selects them), oldest first. None stands for a reply whose
    variant recorded no checks, such as one written before the reply checks existed."""
    rows = connection.execute(
        text(
            "SELECT v.checks_json FROM messages AS m LEFT JOIN message_variants AS v ON v.id = m.active_variant_id "
            "WHERE m.conversation_id = :id AND m.deleted_at IS NULL AND m.seq < :before_seq AND m.author = 'character' "
            "AND m.content != '' AND m.status IN ('complete', 'interrupted') ORDER BY m.seq DESC LIMIT :limit"
        ),
        {"id": conversation_id, "before_seq": before_seq, "limit": limit},
    ).all()
    return [row[0] for row in reversed(rows)]


def streaming_messages(connection: Connection) -> list[Message]:
    """Returns every message whose status is `streaming`, in id order."""
    rows = connection.execute(text(f"{_MESSAGE_SELECT} WHERE m.status = 'streaming' ORDER BY m.id")).mappings().all()
    return [_message(row) for row in rows]


def running_reply_jobs(connection: Connection, message_id: str) -> list[str]:
    """Returns the ids of the running `chat_reply` and `chat_regenerate` jobs of the message, in id order."""
    rows = connection.execute(
        text(
            "SELECT id FROM generation_jobs WHERE message_id = :id AND status = 'running' "
            "AND kind IN ('chat_reply', 'chat_regenerate') ORDER BY id"
        ),
        {"id": message_id},
    ).all()
    return [row[0] for row in rows]


def get_variant(connection: Connection, variant_id: str) -> Variant | None:
    """Returns the variant, or None when no row has that id."""
    row = connection.execute(text(f"{_VARIANT_SELECT} WHERE id = :id"), {"id": variant_id}).mappings().first()
    return None if row is None else _variant(row)


def list_variants(connection: Connection, message_id: str) -> list[Variant]:
    """Returns every variant of the message in `variant_no` order."""
    rows = (
        connection.execute(text(f"{_VARIANT_SELECT} WHERE message_id = :id ORDER BY variant_no"), {"id": message_id})
        .mappings()
        .all()
    )
    return [_variant(row) for row in rows]


def job_exists(connection: Connection, idempotency_key: str) -> bool:
    """Tells whether a job was stored under `idempotency_key`."""
    return (
        connection.execute(
            text("SELECT 1 FROM generation_jobs WHERE idempotency_key = :key"), {"key": idempotency_key}
        ).first()
        is not None
    )


def queue_position(connection: Connection, job_id: str) -> int:
    """Returns how many queued and running jobs run no later than the job (itself included), in the queue's own
    order: priority, then age, then id. 0 when the job is not queued or running."""
    position: int = connection.execute(
        text(
            "SELECT count(*) FROM generation_jobs AS j, generation_jobs AS me "
            "WHERE me.id = :id AND me.status IN ('queued', 'running') AND j.status IN ('queued', 'running') "
            "AND (j.priority < me.priority OR (j.priority = me.priority AND (j.created_at < me.created_at "
            "OR (j.created_at = me.created_at AND j.id <= me.id))))"
        ),
        {"id": job_id},
    ).scalar_one()
    return position
