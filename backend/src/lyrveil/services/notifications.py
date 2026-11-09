"""NotificationService: the list under the bell icon, the unread count and the read marks (docs/scheduler.md,
Notifications; docs/api.md).

Writing a notification belongs to the service that has the news (the proactive delivery, the startup step of the
scheduler); each writes the row in its own transaction with `persistence.notifications.insert_notification` and, once
that transaction committed, calls `announce`. Nothing here is logged: titles and bodies can quote a message.
"""

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.domain.errors import NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.domain.notifications import Notification, notification_document
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import notifications as repository
from lyrveil.ports.events import NOTIFICATION_CREATED, EventPublisher

PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
_NO_NOTIFICATION = "Notifica non trovata."
_BAD_CURSOR = "Il cursore non è valido."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def announce(events: EventPublisher, notification: Notification) -> None:
    """Publishes `notification.created` for a notification whose transaction committed. Must run on the event loop."""
    events.publish(NOTIFICATION_CREATED, {"notification": notification_document(notification)})


def _encode_cursor(notification: Notification) -> str:
    """Returns the opaque cursor that resumes the list below `notification`."""
    return base64.urlsafe_b64encode(f"{notification.created_at}|{notification.id}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Returns the `(created_at, id)` a cursor names. Raises ValidationFailedError for any other text."""
    try:
        created_at, notification_id = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode().split("|")
    except ValueError:
        raise ValidationFailedError(_BAD_CURSOR) from None
    return created_at, notification_id


class NotificationService:
    """Lists and marks the notifications of `engine`. `clock` returns the aware current time; tests pass a fake one.
    Every method blocks: run it in a worker thread."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def list_page(
        self, *, unread_only: bool = False, cursor: str | None = None, limit: int = PAGE_SIZE
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        """Returns one page of documents, newest first, the `cursor` of the next page (None on the last) and the count
        of all unread notifications, whatever the filter. `limit` is clamped to 1..MAX_PAGE_SIZE. Raises
        ValidationFailedError for a `cursor` this service did not issue."""
        after = _decode_cursor(cursor) if cursor is not None else None
        size = max(1, min(limit, MAX_PAGE_SIZE))

        def work(connection: Connection) -> tuple[list[Notification], int]:
            rows = repository.list_page(connection, unread_only=unread_only, after=after, limit=size + 1)
            return rows, repository.unread_count(connection)

        rows, unread = read(self._engine, work)
        shown = rows[:size]
        next_cursor = _encode_cursor(shown[-1]) if len(rows) > size else None
        return [notification_document(row) for row in shown], next_cursor, unread

    def mark_read(self, notification_id: str) -> dict[str, Any]:
        """Marks the notification read and returns its document. Marking a read one again changes nothing. Raises
        NotFoundError for an unknown id."""
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> Notification:
            if not repository.mark_read(connection, notification_id, now):
                raise NotFoundError(_NO_NOTIFICATION)
            marked = repository.get(connection, notification_id)
            assert marked is not None
            return marked

        return notification_document(write(self._engine, work))

    def mark_all_read(self) -> int:
        """Marks every unread notification read and returns how many it changed."""
        now = to_utc_text(self._clock())
        return write(self._engine, lambda connection: repository.mark_all_read(connection, now))

    def get(self, notification_id: str) -> Notification | None:
        """Returns the notification, or None for an unknown id."""
        return read(self._engine, lambda connection: repository.get(connection, notification_id))
