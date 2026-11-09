"""Notification routes of docs/api.md: `GET /notifications`, `POST /notifications/{id}/read` and `POST
/notifications/read-all`. `notification.created` is published by the services that make a notification.

Not in the committed OpenAPI document yet, for the reason given in lyrveil.api.conversations.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Engine

from lyrveil.services.notifications import MAX_PAGE_SIZE, PAGE_SIZE, NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"], include_in_schema=False)


def get_notification_service(request: Request) -> NotificationService:
    """Returns the service on the application's engine; startup always opens it first."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the notification routes need a database")
    return NotificationService(engine)


Notifications = Depends(get_notification_service)


@router.get("")
def list_notifications(
    unread: bool = False,
    cursor: str | None = None,
    limit: int = Query(PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    service: NotificationService = Notifications,
) -> dict[str, Any]:
    """The notifications newest first, only the unread ones with `unread=true`. `unread_count` counts every unread
    notification, not only the page. `next_cursor` is the `cursor` of the next page, null on the last. 422
    `VALIDATION_ERROR` for a `cursor` the server did not issue."""
    items, next_cursor, unread_count = service.list_page(unread_only=unread, cursor=cursor, limit=limit)
    return {"items": items, "next_cursor": next_cursor, "unread_count": unread_count}


# Declared before `/{notification_id}/read` so that "read-all" is never taken for an id.
@router.post("/read-all")
def read_all_notifications(service: NotificationService = Notifications) -> dict[str, int]:
    """Marks every notification read and answers how many it changed."""
    return {"updated": service.mark_all_read()}


@router.post("/{notification_id}/read")
def read_notification(notification_id: str, service: NotificationService = Notifications) -> dict[str, Any]:
    """Marks one notification read and returns it. Calling it again is harmless. 404 `NOT_FOUND` for an unknown id."""
    return service.mark_read(notification_id)
