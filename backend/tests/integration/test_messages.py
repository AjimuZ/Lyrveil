"""Checks messages on a real database: T-MSG-02, T-MSG-04 to T-MSG-06 and T-MSG-08 (integration level)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.errors import ConflictError
from lyrveil.domain.identifiers import new_id
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import write
from lyrveil.main import create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.conversations import ConversationService
from lyrveil.services.messages import MessageService
from lyrveil.settings import Settings

NOW = "2026-11-07T10:00:00.000Z"
API = "/api/v1"


def add_character(engine: Engine) -> str:
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
    return character_id


def count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def finish_reply(engine: Engine, message: dict[str, Any], content: str) -> None:
    """Does what the reply job will do: writes the text into the message's active variant and completes both."""

    def work(connection: Connection) -> None:
        params = {"content": content, "message": message["id"], "variant": message["active_variant_id"]}
        connection.execute(
            text("UPDATE message_variants SET content = :content, status = 'complete' WHERE id = :variant"), params
        )
        connection.execute(
            text("UPDATE messages SET content = :content, status = 'complete' WHERE id = :message"), params
        )

    write(engine, work)


@pytest.fixture
def chat(settings: Settings, engine: Engine) -> str:
    upgrade_to_head(engine)
    return ConversationService(engine).create(add_character(engine)).id


@pytest.fixture
def client(settings: Settings, engine: Engine, chat: str) -> TestClient:
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


def send(client: TestClient, chat: str, content: str = "Ciao", **body: object) -> dict[str, Any]:
    response = client.post(f"{API}/conversations/{chat}/messages", json={"content": content, **body})
    assert response.status_code == 202, response.text
    result: dict[str, Any] = response.json()
    return result


def history(client: TestClient, chat: str, **query: object) -> dict[str, Any]:
    response = client.get(f"{API}/conversations/{chat}/messages", params=query)
    assert response.status_code == 200, response.text
    page: dict[str, Any] = response.json()
    return page


def test_a_send_saves_the_message_and_queues_one_reply_job(client: TestClient, engine: Engine, chat: str) -> None:
    sent = send(client, chat, "Com'è andata?", client_message_id="c-1")
    assert (sent["message"]["seq"], sent["message"]["author"], sent["message"]["status"]) == (1, "user", "complete")
    assert sent["message"]["read_state"] == "sent"
    assert (sent["reply"]["seq"], sent["reply"]["author"], sent["reply"]["status"]) == (2, "character", "queued")
    assert sent["reply"]["content"] == ""
    assert (sent["job"]["kind"], sent["job"]["status"], sent["job"]["queue_position"]) == ("chat_reply", "queued", 1)
    with engine.connect() as connection:
        job = connection.execute(text("SELECT message_id, conversation_id FROM generation_jobs")).one()
    assert tuple(job) == (sent["reply"]["id"], chat)


def test_sending_twice_with_one_client_id_returns_the_first_result_and_writes_nothing(
    client: TestClient, engine: Engine, chat: str
) -> None:
    first = send(client, chat, client_message_id="c-1")
    again = send(client, chat, "Testo diverso", client_message_id="c-1")
    assert again == first
    assert (count(engine, "messages"), count(engine, "message_variants"), count(engine, "generation_jobs")) == (2, 2, 1)
    other = send(client, chat, client_message_id="c-2")
    assert other["message"]["seq"] == 3


def test_the_message_is_saved_before_its_job_and_a_retry_queues_the_missing_job(engine: Engine, chat: str) -> None:
    full = MessageService(engine, JobQueue(max_active=0))
    with pytest.raises(ConflictError):
        full.send(chat, "Salvami", client_message_id="c-1")
    assert count(engine, "generation_jobs") == 0
    saved = full.history(chat).items
    assert [(item.author.value, item.status.value) for item in saved] == [("user", "complete"), ("character", "failed")]
    assert saved[0].content == "Salvami"
    retried = MessageService(engine).send(chat, "Salvami", client_message_id="c-1")
    assert (retried.message.id, retried.reply.id) == (saved[0].id, saved[1].id)
    assert retried.reply.status.value == "queued"
    assert (count(engine, "messages"), count(engine, "generation_jobs")) == (2, 1)


def test_a_send_is_refused_for_blank_or_oversized_text_and_unusable_chats(
    client: TestClient, engine: Engine, chat: str
) -> None:
    url = f"{API}/conversations/{chat}/messages"
    blank = client.post(url, json={"content": "  "})
    assert (blank.status_code, blank.json()["code"]) == (422, "VALIDATION_ERROR")
    large = client.post(url, json={"content": "x" * 8001})
    assert (large.status_code, large.json()["code"]) == (413, "PAYLOAD_TOO_LARGE")
    assert client.post(url, json={"content": "x" * 8000}).status_code == 202
    assert client.post(f"{API}/conversations/missing/messages", json={"content": "a"}).status_code == 404
    client.patch(f"{API}/conversations/{chat}", json={"archived": True})
    assert client.post(url, json={"content": "a"}).status_code == 409
    client.patch(f"{API}/conversations/{chat}", json={"archived": False})
    client.delete(f"{API}/conversations/{chat}")
    assert client.post(url, json={"content": "a"}).status_code == 404
    assert client.get(url).status_code == 404


def test_regenerating_adds_a_variant_and_alternatives_can_be_selected(
    client: TestClient, engine: Engine, chat: str
) -> None:
    reply = send(client, chat)["reply"]
    busy = client.post(f"{API}/messages/{reply['id']}/regenerate")
    assert (busy.status_code, busy.json()["code"]) == (409, "CONFLICT")
    finish_reply(engine, reply, "Prima risposta")

    regenerated = client.post(f"{API}/messages/{reply['id']}/regenerate")
    assert regenerated.status_code == 202, regenerated.text
    body = regenerated.json()
    assert (body["message"]["status"], body["message"]["content"], body["message"]["variant_count"]) == (
        "queued",
        "",
        2,
    )
    assert body["message"]["active_variant_id"] == body["variant_id"] != reply["active_variant_id"]
    assert body["job"]["kind"] == "chat_regenerate"
    finish_reply(engine, body["message"], "Seconda risposta")

    variants = client.get(f"{API}/messages/{reply['id']}/variants").json()["items"]
    assert [(item["variant_no"], item["source"], item["content"]) for item in variants] == [
        (1, "generated", "Prima risposta"),
        (2, "generated", "Seconda risposta"),
    ]
    first = client.post(f"{API}/messages/{reply['id']}/variants/{variants[0]['id']}/activate")
    assert first.status_code == 200
    assert (first.json()["content"], first.json()["status"], first.json()["variant_count"]) == (
        "Prima risposta",
        "complete",
        2,
    )
    assert history(client, chat)["items"][-1]["content"] == "Prima risposta"
    assert client.post(f"{API}/messages/{reply['id']}/variants/{new_id()}/activate").status_code == 404


def test_only_the_last_character_reply_can_be_regenerated_or_switched(
    client: TestClient, engine: Engine, chat: str
) -> None:
    first = send(client, chat)
    finish_reply(engine, first["reply"], "Uno")
    second = send(client, chat)
    finish_reply(engine, second["reply"], "Due")
    for target in (first["reply"]["id"], second["message"]["id"]):
        assert client.post(f"{API}/messages/{target}/regenerate").status_code == 409
    variant = client.get(f"{API}/messages/{first['reply']['id']}/variants").json()["items"][0]["id"]
    assert client.post(f"{API}/messages/{first['reply']['id']}/variants/{variant}/activate").status_code == 409
    assert client.post(f"{API}/messages/{second['reply']['id']}/regenerate").status_code == 202


def test_a_full_queue_marks_the_new_variant_failed_and_keeps_the_old_one_selectable(engine: Engine, chat: str) -> None:
    service = MessageService(engine)
    reply = service.send(chat, "Ciao").reply
    finish_reply(engine, {"id": reply.id, "active_variant_id": reply.active_variant_id}, "Risposta")
    with pytest.raises(ConflictError):
        MessageService(engine, JobQueue(max_active=0)).regenerate(reply.id)
    failed = service.history(chat).items[-1]
    assert (failed.status.value, failed.variant_count) == ("failed", 2)
    original = service.variants(reply.id)[0]
    restored = service.activate_variant(reply.id, original.id)
    assert (restored.active_variant_id, restored.content, restored.status.value) == (
        original.id,
        "Risposta",
        "complete",
    )


def test_an_edit_adds_an_edited_variant_and_refuses_pending_or_invalid_text(
    client: TestClient, engine: Engine, chat: str
) -> None:
    sent = send(client, chat, "Testo")
    user, reply = sent["message"], sent["reply"]
    assert client.patch(f"{API}/messages/{reply['id']}", json={"content": "x"}).status_code == 409
    edited = client.patch(f"{API}/messages/{user['id']}", json={"content": "Testo corretto"})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["content"], edited.json()["variant_count"]) == ("Testo corretto", 2)
    assert edited.json()["edited_at"] is not None
    variants = client.get(f"{API}/messages/{user['id']}/variants").json()["items"]
    assert [(item["source"], item["status"]) for item in variants] == [("user", "complete"), ("edited", "complete")]
    assert client.patch(f"{API}/messages/{user['id']}", json={"content": "Testo corretto"}).json()["variant_count"] == 2
    assert client.patch(f"{API}/messages/{user['id']}", json={"content": " "}).status_code == 422
    assert client.patch(f"{API}/messages/{user['id']}", json={"content": "x" * 8001}).status_code == 413
    finish_reply(engine, reply, "Risposta")
    assert client.patch(f"{API}/messages/{reply['id']}", json={"content": "Risposta migliore"}).status_code == 200
    assert history(client, chat)["items"][1]["content"] == "Risposta migliore"
    assert client.patch(f"{API}/messages/missing", json={"content": "x"}).status_code == 404


def test_a_deleted_message_leaves_the_history_but_its_row_stays(client: TestClient, engine: Engine, chat: str) -> None:
    sent = send(client, chat)
    user, reply = sent["message"], sent["reply"]
    assert client.delete(f"{API}/messages/{reply['id']}").status_code == 409
    assert client.delete(f"{API}/messages/{user['id']}").status_code == 204
    assert [item["id"] for item in history(client, chat)["items"]] == [reply["id"]]
    assert client.delete(f"{API}/messages/{user['id']}").status_code == 404
    assert client.patch(f"{API}/messages/{user['id']}", json={"content": "x"}).status_code == 404
    assert client.get(f"{API}/messages/{user['id']}/variants").status_code == 404
    assert count(engine, "messages") == 2
    with engine.connect() as connection:
        deleted_at = connection.execute(
            text("SELECT deleted_at FROM messages WHERE id = :id"), {"id": user["id"]}
        ).scalar_one()
    assert deleted_at is not None
    next_message = send(client, chat)
    assert next_message["message"]["seq"] == 3


def test_history_pages_newest_first_without_gaps_and_shows_pending_replies(
    client: TestClient, engine: Engine, chat: str
) -> None:
    service = MessageService(engine)
    for number in range(30):
        service.send(chat, f"Messaggio {number}", client_message_id=f"c-{number}")
    first = history(client, chat, limit=25)
    assert [item["seq"] for item in first["items"]] == list(range(36, 61))
    assert first["next_before_seq"] == 36
    assert first["items"][-1]["status"] == "queued"
    second = history(client, chat, limit=25, before_seq=first["next_before_seq"])
    assert [item["seq"] for item in second["items"]] == list(range(11, 36))
    last = history(client, chat, limit=25, before_seq=second["next_before_seq"])
    assert [item["seq"] for item in last["items"]] == list(range(1, 11))
    assert last["next_before_seq"] is None
    exact = history(client, chat, limit=60)
    assert (len(exact["items"]), exact["next_before_seq"]) == (60, None)
    assert history(client, chat)["items"][0]["seq"] == 11
    assert client.get(f"{API}/conversations/{chat}/messages", params={"limit": 201}).status_code == 422
    assert client.get(f"{API}/conversations/{chat}/messages", params={"limit": 0}).status_code == 422
