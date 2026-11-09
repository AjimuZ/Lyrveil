"""SQL of `notifications`: the in-app notices (docs/scheduler.md, Notifications; docs/data-model.md).

Every function works on a connection the caller owns and never commits. The list is newest first with `id` as the
tie-break, which is the order of the index on `created_at`.
"""

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.identifiers import new_id
from lyrveil.domain.notifications import Notification

# The kinds the table accepts (CHECK ck_notifications_kind).
PROACTIVE_MESSAGE = "proactive_message"
MISSED_EVENTS = "missed_events"

_COLUMNS = "id, kind, conversation_id, message_id, title, body, read_at, created_at"


def _notification(row: RowMapping) -> Notification:
    return Notification(
        id=row["id"],
        kind=row["kind"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        title=row["title"],
        body=row["body"],
        read_at=row["read_at"],
        created_at=row["created_at"],
    )


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


def get(connection: Connection, notification_id: str) -> Notification | None:
    """Returns the notification, or None when no row has that id."""
    row = (
        connection.execute(text(f"SELECT {_COLUMNS} FROM notifications WHERE id = :id"), {"id": notification_id})
        .mappings()
        .first()
    )
    return None if row is None else _notification(row)


def list_page(
    connection: Connection, *, unread_only: bool, after: tuple[str, str] | None, limit: int
) -> list[Notification]:
    """Returns up to `limit` notifications, newest first, only the unread ones when `unread_only`. `after` is the
    `(created_at, id)` of the last row of the previous page: the rows below it follow."""
    conditions = []
    if unread_only:
        conditions.append("read_at IS NULL")
    if after is not None:
        conditions.append("(created_at < :created_at OR (created_at = :created_at AND id < :id))")
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    rows = connection.execute(
        text(f"SELECT {_COLUMNS} FROM notifications {where}ORDER BY created_at DESC, id DESC LIMIT :limit"),
        {
            "created_at": None if after is None else after[0],
            "id": None if after is None else after[1],
            "limit": limit,
        },
    ).mappings()
    return [_notification(row) for row in rows]


def unread_count(connection: Connection) -> int:
    """Returns how many notifications are unread."""
    return int(connection.execute(text("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL")).scalar_one())


def mark_read(connection: Connection, notification_id: str, now: str) -> bool:
    """Stamps the notification read at `now` unless it already is (the first read time stays). Returns whether a
    notification with that id exists."""
    connection.execute(
        text("UPDATE notifications SET read_at = :now WHERE id = :id AND read_at IS NULL"),
        {"id": notification_id, "now": now},
    )
    exists = connection.execute(text("SELECT 1 FROM notifications WHERE id = :id"), {"id": notification_id}).first()
    return exists is not None


def mark_all_read(connection: Connection, now: str) -> int:
    """Stamps every unread notification read at `now` and returns how many it changed."""
    result = connection.execute(text("UPDATE notifications SET read_at = :now WHERE read_at IS NULL"), {"now": now})
    return result.rowcount
