"""Checks conversations through the API on a real database: T-MSG-01, and T-MSG-11 without search (P02.5d)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import new_id
from lyrveil.jobs.scheduler import write
from lyrveil.main import create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.conversations import ConversationService
from lyrveil.settings import Settings

NOW = "2026-11-07T10:00:00.000Z"


def add_character(
    engine: Engine, first_name: str = "Alba", status: str = "active", last_name: str | None = None
) -> str:
    character_id = new_id()
    write(
        engine,
        lambda connection: connection.execute(
            text(
                "INSERT INTO characters (id, first_name, last_name, age, status, created_at, updated_at) "
                "VALUES (:id, :first, :last, 30, :status, :now, :now)"
            ),
            {"id": character_id, "first": first_name, "last": last_name, "status": status, "now": NOW},
        ),
    )
    return character_id


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    upgrade_to_head(engine)
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


def create(client: TestClient, character_id: str, **body: object) -> dict[str, object]:
    response = client.post("/api/v1/conversations", json={"character_id": character_id, **body})
    assert response.status_code == 201, response.text
    body_json: dict[str, object] = response.json()
    return body_json


def listed(client: TestClient, **query: str) -> list[str]:
    response = client.get("/api/v1/conversations", params=query)
    assert response.status_code == 200
    return [item["id"] for item in response.json()["items"]]


def test_a_chat_is_created_for_a_character(client: TestClient, engine: Engine) -> None:
    character_id = add_character(engine, "Alba", last_name="Rossi")
    created = create(client, character_id)
    assert created["title"] == "Alba Rossi"
    assert (created["status"], created["pinned"], created["unread_count"]) == ("active", False, 0)
    assert created["effective_language"] == "it"
    assert client.get(f"/api/v1/conversations/{created['id']}").json() == created
    assert listed(client) == [created["id"]]


def test_a_chat_needs_an_existing_active_character(client: TestClient, engine: Engine) -> None:
    archived = add_character(engine, status="archived")
    missing = client.post("/api/v1/conversations", json={"character_id": new_id()})
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
    refused = client.post("/api/v1/conversations", json={"character_id": archived})
    assert (refused.status_code, refused.json()["code"]) == (409, "CONFLICT")
    assert listed(client) == []


def test_pinned_chats_come_first_and_archived_ones_leave_the_main_list(client: TestClient, engine: Engine) -> None:
    character_id = add_character(engine)
    first = create(client, character_id)["id"]
    second = create(client, character_id)["id"]
    third = create(client, character_id)["id"]
    # Creation times can tie within a millisecond; the order under test needs distinct activity times.
    for offset, conversation_id in enumerate((first, second, third)):

        def set_time(connection: Connection, offset: int = offset, conversation_id: object = conversation_id) -> None:
            connection.execute(
                text("UPDATE conversations SET created_at = :at WHERE id = :id"),
                {"at": f"2026-11-07T10:00:0{offset}.000Z", "id": conversation_id},
            )

        write(engine, set_time)
    assert listed(client) == [third, second, first]
    pinned = client.patch(f"/api/v1/conversations/{first}", json={"pinned": True}).json()
    assert pinned["pinned"] is True
    assert listed(client) == [first, third, second]
    client.patch(f"/api/v1/conversations/{second}", json={"archived": True})
    assert listed(client) == [first, third]
    assert listed(client, status="archived") == [second]
    client.patch(f"/api/v1/conversations/{second}", json={"archived": False})
    client.patch(f"/api/v1/conversations/{first}", json={"pinned": False})
    assert listed(client) == [third, second, first]


def test_a_patch_changes_only_the_fields_it_names(client: TestClient, engine: Engine) -> None:
    created = create(client, add_character(engine), title="Prima")
    patched = client.patch(
        f"/api/v1/conversations/{created['id']}", json={"language": "en", "shares_memory": True}
    ).json()
    assert (patched["title"], patched["language"], patched["effective_language"]) == ("Prima", "en", "en")
    assert patched["shares_memory"] is True
    reset = client.patch(f"/api/v1/conversations/{created['id']}", json={"title": " ", "language": None}).json()
    assert (reset["title"], reset["effective_language"]) == ("Alba", "it")


def test_a_deleted_chat_is_hidden_everywhere_and_comes_back_pinned_as_it_was(
    client: TestClient, engine: Engine
) -> None:
    created = create(client, add_character(engine))
    conversation_id = str(created["id"])
    client.patch(f"/api/v1/conversations/{conversation_id}", json={"pinned": True})
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    assert listed(client) == []
    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
    assert client.patch(f"/api/v1/conversations/{conversation_id}", json={"pinned": False}).status_code == 404
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 404
    assert listed(client, status="deleted") == [conversation_id]
    restored = client.post(f"/api/v1/conversations/{conversation_id}/undelete")
    assert restored.status_code == 200
    assert (restored.json()["status"], restored.json()["pinned"]) == ("active", True)
    assert listed(client) == [conversation_id]


def test_deletion_keeps_the_row_and_restoring_something_not_deleted_conflicts(
    client: TestClient, engine: Engine
) -> None:
    conversation_id = str(create(client, add_character(engine))["id"])
    conflict = client.post(f"/api/v1/conversations/{conversation_id}/undelete")
    assert (conflict.status_code, conflict.json()["code"]) == (409, "CONFLICT")
    client.delete(f"/api/v1/conversations/{conversation_id}")
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, deleted_at FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).one()
    assert row[0] == "deleted"
    assert row[1] is not None
    assert client.post("/api/v1/conversations/missing/undelete").status_code == 404


def test_a_chat_deleted_more_than_thirty_days_ago_cannot_be_restored(engine: Engine) -> None:
    upgrade_to_head(engine)
    moment = [datetime(2026, 11, 7, 10, 0, tzinfo=UTC)]
    service = ConversationService(engine, clock=lambda: moment[0])
    conversation_id = service.create(add_character(engine)).id
    service.delete(conversation_id)
    moment[0] += timedelta(days=30, seconds=1)
    with pytest.raises(ConflictError):
        service.restore(conversation_id)
    moment[0] -= timedelta(seconds=2)
    assert service.restore(conversation_id).deleted_at is None
    with pytest.raises(NotFoundError):
        service.delete("missing")
