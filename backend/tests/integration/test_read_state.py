"""T-MSG-10 (P07.6): read state and unread counts through the API on a migrated database: the unread count of the chat
list, `POST /conversations/{id}/read`, the simulated read state of the user's messages and the preview and activity
hints of a list row."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.identifiers import new_id
from lyrveil.jobs.scheduler import write
from lyrveil.main import create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.conversations import ConversationService
from lyrveil.settings import Settings

NOW = "2026-11-07T10:00:00.000Z"
API = "/api/v1"


@pytest.fixture
def chat(settings: Settings, engine: Engine) -> str:
    upgrade_to_head(engine)
    character_id = new_id()

    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, status, created_at, updated_at) "
                "VALUES (:id, 'Alba', 30, 'active', :now, :now)"
            ),
            {"id": character_id, "now": NOW},
        )

    write(engine, work)
    return ConversationService(engine).create(character_id).id


@pytest.fixture
def client(settings: Settings, engine: Engine, chat: str) -> TestClient:
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


def send(client: TestClient, chat: str, content: str = "Ciao") -> dict[str, Any]:
    response = client.post(f"{API}/conversations/{chat}/messages", json={"content": content})
    assert response.status_code == 202, response.text
    result: dict[str, Any] = response.json()
    return result


def set_reply(engine: Engine, message: dict[str, Any], status: str, content: str = "") -> None:
    """Moves the reply the way the reply job does: its text and status, on the message and its active variant."""

    def work(connection: Connection) -> None:
        connection.execute(
            text("UPDATE message_variants SET content = :content, status = :status WHERE id = :variant"),
            {"content": content, "status": status, "variant": message["active_variant_id"]},
        )
        connection.execute(
            text("UPDATE messages SET content = :content, status = :status WHERE id = :message"),
            {"content": content, "status": status, "message": message["id"]},
        )

    write(engine, work)


def row(client: TestClient, chat: str) -> dict[str, Any]:
    items = client.get(f"{API}/conversations").json()["items"]
    found: dict[str, Any] = next(item for item in items if item["id"] == chat)
    return found


def user_message(client: TestClient, chat: str) -> dict[str, Any]:
    items = client.get(f"{API}/conversations/{chat}/messages").json()["items"]
    found: dict[str, Any] = next(item for item in items if item["author"] == "user")
    return found


def test_only_finished_character_messages_are_unread_and_the_row_previews_the_newest(
    client: TestClient, engine: Engine, chat: str
) -> None:
    reply = send(client, chat, "Come stai?")["reply"]
    waiting = row(client, chat)
    assert (waiting["unread_count"], waiting["activity"]) == (0, "queued")
    assert waiting["last_message"] == {"author": "user", "kind": "text", "text": "Come stai?"}

    set_reply(engine, reply, "streaming", "Be")
    assert (row(client, chat)["unread_count"], row(client, chat)["activity"]) == (0, "typing")

    set_reply(engine, reply, "complete", "Bene, grazie!")
    done = row(client, chat)
    assert (done["unread_count"], done["activity"]) == (1, None)
    assert done["last_message"] == {"author": "character", "kind": "text", "text": "Bene, grazie!"}


def test_a_long_reply_is_cut_in_the_preview(client: TestClient, engine: Engine, chat: str) -> None:
    reply = send(client, chat)["reply"]
    set_reply(engine, reply, "complete", "parola " * 60)
    preview = row(client, chat)["last_message"]["text"]
    assert len(preview) <= 141
    assert preview.endswith("…")


def test_reading_clears_the_count_moves_only_forward_and_never_passes_the_last_message(
    client: TestClient, engine: Engine, chat: str
) -> None:
    first = send(client, chat)["reply"]
    set_reply(engine, first, "complete", "Uno")
    assert row(client, chat)["unread_count"] == 1

    marked = client.post(f"{API}/conversations/{chat}/read", json={"up_to_seq": 2})
    assert marked.status_code == 200
    assert marked.json()["unread_count"] == 0

    second = send(client, chat)["reply"]
    set_reply(engine, second, "complete", "Due")
    assert row(client, chat)["unread_count"] == 1
    # An older mark does not bring the first reply back, and a mark far ahead stops at the last message taken.
    client.post(f"{API}/conversations/{chat}/read", json={"up_to_seq": 1})
    assert row(client, chat)["unread_count"] == 1
    client.post(f"{API}/conversations/{chat}/read", json={"up_to_seq": 999})
    assert row(client, chat)["unread_count"] == 0
    third = send(client, chat)["reply"]
    set_reply(engine, third, "complete", "Tre")
    assert row(client, chat)["unread_count"] == 1


def test_reading_does_not_count_as_activity(client: TestClient, engine: Engine, chat: str) -> None:
    set_reply(engine, send(client, chat)["reply"], "complete", "Ciao")
    before = row(client, chat)
    after = client.post(f"{API}/conversations/{chat}/read", json={"up_to_seq": 2}).json()
    assert (after["updated_at"], after["last_message_at"]) == (before["updated_at"], before["last_message_at"])


def test_the_read_route_refuses_a_missing_chat_and_a_bad_mark(client: TestClient, chat: str) -> None:
    missing = client.post(f"{API}/conversations/{new_id()}/read", json={"up_to_seq": 1})
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
    bad = client.post(f"{API}/conversations/{chat}/read", json={"up_to_seq": -1})
    # A body the model rejects is answered by FastAPI itself, so only the status is part of the contract here.
    assert bad.status_code == 422
    assert client.post(f"{API}/conversations/{chat}/read", json={}).status_code == 422


def test_a_user_message_is_read_once_the_character_starts_answering(
    client: TestClient, engine: Engine, chat: str
) -> None:
    sent = send(client, chat)
    assert user_message(client, chat)["read_state"] == "sent"
    set_reply(engine, sent["reply"], "streaming", "Ci")
    assert user_message(client, chat)["read_state"] == "read"


def test_a_deleted_chat_cannot_be_marked_read(client: TestClient, chat: str) -> None:
    assert client.delete(f"{API}/conversations/{chat}").status_code == 204
    assert client.post(f"{API}/conversations/{chat}/read", json={"up_to_seq": 1}).status_code == 404
