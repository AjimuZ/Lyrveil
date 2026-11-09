"""P07.5: the notification routes on a migrated database: the list with its unread count and cursor, the read marks, and
the `notification.created` event that announces a new notification on the event hub.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from lyrveil.domain.identifiers import to_utc_text
from lyrveil.events.hub import EventHub
from lyrveil.jobs.scheduler import write
from lyrveil.main import create_app
from lyrveil.persistence import notifications as repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.notifications import NotificationService, announce
from lyrveil.settings import Settings

API = "/api/v1"
START = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    upgrade_to_head(engine)
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


def add_notification(engine: Engine, minutes: int, *, kind: str = "missed_events", title: str = "Titolo") -> str:
    """Stores an unread notification created `minutes` after START and returns its id."""
    stamp = to_utc_text(START + timedelta(minutes=minutes))
    return write(
        engine,
        lambda connection: repository.insert_notification(
            connection, kind=kind, title=title, body=f"Corpo {minutes}", now=stamp
        ),
    )


def listing(client: TestClient, query: str = "") -> dict[str, Any]:
    response = client.get(f"{API}/notifications{query}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_the_list_is_newest_first_with_the_unread_count(client: TestClient, engine: Engine) -> None:
    first = add_notification(engine, 1)
    second = add_notification(engine, 2)

    body = listing(client)

    assert [item["id"] for item in body["items"]] == [second, first]
    assert body["unread_count"] == 2
    assert body["next_cursor"] is None
    newest = body["items"][0]
    assert newest["kind"] == "missed_events"
    assert newest["read"] is False
    assert newest["read_at"] is None
    assert newest["conversation_id"] is None


def test_an_empty_list_has_no_unread(client: TestClient) -> None:
    assert listing(client) == {"items": [], "next_cursor": None, "unread_count": 0}


def test_reading_one_notification_marks_it_and_lowers_the_count(client: TestClient, engine: Engine) -> None:
    kept = add_notification(engine, 1)
    marked = add_notification(engine, 2)

    response = client.post(f"{API}/notifications/{marked}/read")

    assert response.status_code == 200
    assert response.json()["read"] is True
    assert response.json()["read_at"] is not None
    body = listing(client)
    assert body["unread_count"] == 1
    assert [item["read"] for item in body["items"]] == [True, False]
    assert [item["id"] for item in listing(client, "?unread=true")["items"]] == [kept]


def test_reading_again_keeps_the_first_read_time(client: TestClient, engine: Engine) -> None:
    notification = add_notification(engine, 1)
    first = client.post(f"{API}/notifications/{notification}/read").json()

    again = client.post(f"{API}/notifications/{notification}/read")

    assert again.status_code == 200
    assert again.json()["read_at"] == first["read_at"]


def test_an_unknown_notification_is_not_found(client: TestClient) -> None:
    response = client.post(f"{API}/notifications/nope/read")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_read_all_marks_every_unread_notification(client: TestClient, engine: Engine) -> None:
    add_notification(engine, 1)
    add_notification(engine, 2)
    already = add_notification(engine, 3)
    client.post(f"{API}/notifications/{already}/read")

    response = client.post(f"{API}/notifications/read-all")

    assert response.status_code == 200
    assert response.json() == {"updated": 2}
    body = listing(client)
    assert body["unread_count"] == 0
    assert all(item["read"] for item in body["items"])
    assert listing(client, "?unread=true")["items"] == []


def test_pages_follow_the_cursor_and_the_count_covers_every_page(client: TestClient, engine: Engine) -> None:
    ids = [add_notification(engine, minute) for minute in range(1, 6)]

    first = listing(client, "?limit=2")
    second = listing(client, f"?limit=2&cursor={first['next_cursor']}")
    third = listing(client, f"?limit=2&cursor={second['next_cursor']}")

    assert [item["id"] for item in first["items"]] == [ids[4], ids[3]]
    assert [item["id"] for item in second["items"]] == [ids[2], ids[1]]
    assert [item["id"] for item in third["items"]] == [ids[0]]
    assert third["next_cursor"] is None
    assert first["unread_count"] == 5


def test_a_cursor_the_server_did_not_issue_is_refused(client: TestClient) -> None:
    response = client.get(f"{API}/notifications?cursor=not-a-cursor")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_announce_publishes_the_new_notification_on_the_hub(engine: Engine) -> None:
    """`notification.created` carries the same document the list returns."""
    upgrade_to_head(engine)
    created = add_notification(engine, 1, kind="proactive_message", title="Alba")
    hub = EventHub("boot")
    notification = NotificationService(engine).get(created)
    assert notification is not None

    announce(hub, notification)

    published = hub.missed(0, hub.boot_id)
    assert published is not None
    assert [event.type for event in published] == ["notification.created"]
    document = published[0].data["notification"]
    assert document["id"] == created
    assert document["kind"] == "proactive_message"
    assert document["title"] == "Alba"
    assert document["read"] is False
