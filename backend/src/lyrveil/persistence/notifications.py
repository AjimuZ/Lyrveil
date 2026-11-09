"""SQL of `notifications`: the in-app notices (docs/scheduler.md, Notifications; docs/data-model.md).

Every function works on a connection the caller owns and never commits. Only the write side exists so far; the list,
the read marks and the `notification.created` event are P07.5's.
"""

from sqlalchemy import Connection, text

from lyrveil.domain.identifiers import new_id

# The kinds the table accepts (CHECK ck_notifications_kind).
MISSED_EVENTS = "missed_events"


def insert_notification(
    connection: Connection,
    *,
    kind: str,
    title: str,
    body: str,
    now: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> str:
    """Writes an unread notification stamped `now` and returns its id. `kind` must be one the table lists, otherwise
    the database refuses the row."""
    notification_id = new_id()
    connection.execute(
        text(
            "INSERT INTO notifications (id, kind, conversation_id, message_id, title, body, read_at, created_at) "
            "VALUES (:id, :kind, :conversation_id, :message_id, :title, :body, NULL, :now)"
        ),
        {
            "id": notification_id,
            "kind": kind,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "title": title,
            "body": body,
            "now": now,
        },
    )
    return notification_id
