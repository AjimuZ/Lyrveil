"""Notification: the in-app notice of docs/scheduler.md (Notifications) and the document the API and events carry."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Notification:
    """One row of `notifications`. `read_at` is None while the notice is unread. `conversation_id` and `message_id`
    are None for a notice about no chat (`missed_events`) and after the chat or the message was deleted."""

    id: str
    kind: str
    conversation_id: str | None
    message_id: str | None
    title: str
    body: str
    read_at: str | None
    created_at: str


def notification_document(notification: Notification) -> dict[str, Any]:
    """Returns the JSON document of docs/api.md: the row's fields and `read`, true once the notice was read."""
    return {
        "id": notification.id,
        "kind": notification.kind,
        "conversation_id": notification.conversation_id,
        "message_id": notification.message_id,
        "title": notification.title,
        "body": notification.body,
        "read": notification.read_at is not None,
        "read_at": notification.read_at,
        "created_at": notification.created_at,
    }
