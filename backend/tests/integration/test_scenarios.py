"""Checks scenarios and several conversations per character through the API on a real database (P03.7a).

Covers the scenario CRUD, a conversation started from a scenario, and that one conversation's context never holds
another's (the integration half of T-CON-02; the full test follows in P04).
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from lyrveil.domain.identifiers import new_id
from lyrveil.events.hub import EventHub
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import JobScheduler, write
from lyrveil.main import create_app
from lyrveil.orchestration.client import Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.services.chat_inference import ChatInferenceService
from lyrveil.services.conversations import ConversationService
from lyrveil.services.messages import MessageService
from lyrveil.services.scenarios import ScenarioService
from lyrveil.settings import Settings

NOW = "2026-11-07T10:00:00.000Z"
API = "/api/v1"
LIGHTHOUSE = {
    "title": "Sera al faro",
    "description": "Una sera di pioggia al faro vecchio.",
    "setting": "Una scogliera battuta dal vento.",
    "opening_message": "Hai fatto tardi, ormai pensavo non venissi più.",
}


def add_character(engine: Engine, first_name: str = "Alba") -> str:
    character_id = new_id()
    write(
        engine,
        lambda connection: connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, status, created_at, updated_at) "
                "VALUES (:id, :name, 30, 'active', :now, :now)"
            ),
            {"id": character_id, "name": first_name, "now": NOW},
        ),
    )
    return character_id


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    upgrade_to_head(engine)
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


def make_scenario(client: TestClient, **body: object) -> dict[str, Any]:
    response = client.post(f"{API}/scenarios", json={**LIGHTHOUSE, **body})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def start_chat(client: TestClient, character_id: str, **body: object) -> dict[str, Any]:
    response = client.post(f"{API}/conversations", json={"character_id": character_id, **body})
    assert response.status_code == 201, response.text
    started: dict[str, Any] = response.json()
    return started


def scenario_ids(client: TestClient, **query: str) -> list[str]:
    response = client.get(f"{API}/scenarios", params=query)
    assert response.status_code == 200
    return [item["id"] for item in response.json()["items"]]


def test_a_character_has_several_conversations_and_the_list_shows_each_one(client: TestClient, engine: Engine) -> None:
    alba, other = add_character(engine, "Alba"), add_character(engine, "Bruno")
    first, second, third = (start_chat(client, alba, title=title)["id"] for title in ("Uno", "Due", "Tre"))
    elsewhere = start_chat(client, other)["id"]
    everything = [item["id"] for item in client.get(f"{API}/conversations").json()["items"]]
    assert sorted(everything) == sorted([first, second, third, elsewhere])
    mine = client.get(f"{API}/conversations", params={"character_id": alba}).json()["items"]
    assert sorted(item["id"] for item in mine) == sorted([first, second, third])
    assert {item["character_id"] for item in mine} == {alba}


def test_a_scenario_is_created_edited_archived_and_deleted(client: TestClient) -> None:
    created = make_scenario(client)
    assert (created["status"], created["character_id"], created["language"]) == ("active", None, None)
    assert client.get(f"{API}/scenarios/{created['id']}").json() == created

    edited = client.put(f"{API}/scenarios/{created['id']}", json={**LIGHTHOUSE, "title": "  Mattina al porto  "})
    assert edited.status_code == 200
    assert (edited.json()["title"], edited.json()["description"]) == ("Mattina al porto", LIGHTHOUSE["description"])

    assert client.post(f"{API}/scenarios/{created['id']}/archive").json()["status"] == "archived"
    assert scenario_ids(client) == []
    assert scenario_ids(client, status="archived") == [created["id"]]
    assert client.post(f"{API}/scenarios/{created['id']}/unarchive").json()["status"] == "active"

    assert client.delete(f"{API}/scenarios/{created['id']}").status_code == 204
    assert client.get(f"{API}/scenarios/{created['id']}").status_code == 404
    assert client.delete(f"{API}/scenarios/{created['id']}").status_code == 404
    assert scenario_ids(client) == []


def test_scenarios_are_searched_and_filtered_by_character(client: TestClient, engine: Engine) -> None:
    alba, bruno = add_character(engine, "Alba"), add_character(engine, "Bruno")
    anyone = make_scenario(client)["id"]
    only_alba = make_scenario(client, title="Caffè del mattino", description="Perché no?", character_id=alba)["id"]
    assert sorted(scenario_ids(client)) == sorted([anyone, only_alba])
    assert sorted(scenario_ids(client, character_id=alba)) == sorted([anyone, only_alba])
    assert scenario_ids(client, character_id=bruno) == [anyone]
    assert scenario_ids(client, q="PERCHE") == [only_alba]
    assert scenario_ids(client, q="faro vento") == [anyone]
    assert scenario_ids(client, q="inesistente") == []


def test_a_scenario_refuses_a_blank_title_an_unknown_character_and_a_text_about_a_minor(client: TestClient) -> None:
    blank = client.post(f"{API}/scenarios", json={**LIGHTHOUSE, "title": "   "})
    assert (blank.status_code, blank.json()["code"]) == (422, "VALIDATION_ERROR")
    unknown = client.post(f"{API}/scenarios", json={**LIGHTHOUSE, "character_id": new_id()})
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")
    minor = client.post(f"{API}/scenarios", json={**LIGHTHOUSE, "description": "Una ragazza di 15 anni."})
    assert (minor.status_code, minor.json()["code"]) == (422, "CONTENT_POLICY_BLOCKED")
    assert "descrizione" in minor.json()["detail"]
    assert scenario_ids(client) == []


def test_a_conversation_starts_from_a_scenario_and_keeps_its_own_copy(client: TestClient, engine: Engine) -> None:
    character_id = add_character(engine)
    scenario = make_scenario(client, language="en")
    chat = start_chat(client, character_id, scenario_id=scenario["id"])
    assert (chat["scenario_id"], chat["scenario_title"], chat["language"]) == (scenario["id"], "Sera al faro", "en")

    history = client.get(f"{API}/conversations/{chat['id']}/messages").json()["items"]
    assert [(item["author"], item["status"], item["content"]) for item in history] == [
        ("character", "complete", LIGHTHOUSE["opening_message"])
    ]

    client.put(f"{API}/scenarios/{scenario['id']}", json={**LIGHTHOUSE, "title": "Cambiato", "opening_message": "No"})
    assert client.get(f"{API}/conversations/{chat['id']}").json()["scenario_title"] == "Sera al faro"
    client.delete(f"{API}/scenarios/{scenario['id']}")
    after = client.get(f"{API}/conversations/{chat['id']}").json()
    assert (after["scenario_id"], after["scenario_title"]) == (None, "Sera al faro")


def test_a_scenario_cannot_start_a_conversation_when_it_is_missing_archived_or_for_another_character(
    client: TestClient, engine: Engine
) -> None:
    alba, bruno = add_character(engine, "Alba"), add_character(engine, "Bruno")
    archived = make_scenario(client)["id"]
    client.post(f"{API}/scenarios/{archived}/archive")
    for_alba = make_scenario(client, character_id=alba)["id"]
    cases = [(alba, new_id(), 404), (alba, archived, 409), (bruno, for_alba, 409)]
    for character_id, scenario_id, status in cases:
        response = client.post(f"{API}/conversations", json={"character_id": character_id, "scenario_id": scenario_id})
        assert response.status_code == status, response.text
    assert client.get(f"{API}/conversations").json()["items"] == []
    assert start_chat(client, alba, scenario_id=for_alba)["scenario_id"] == for_alba


class RecordingRuntime:
    """A TextRuntime that answers a fixed word and keeps every prompt it was given."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        self.prompts.append(request.prompt)
        yield TextChunk("Va bene")
        yield TextChunk("", "stop", "Va bene")

    async def generate(self, request: TextRequest) -> str:
        return "Va bene"

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


class Leases:
    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        return Lease(id=f"lease-{job_id}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)

    def renew_lease(self, lease: Lease) -> Lease:
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        return True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_nothing_from_one_conversation_reaches_the_context_of_another(engine: Engine) -> None:
    upgrade_to_head(engine)
    character_id = add_character(engine)
    scenario = ScenarioService(engine).create(
        {"title": "Faro-ALPHA", "description": "Descrizione-ALPHA", "opening_message": "Apertura-ALPHA"}
    )
    conversations = ConversationService(engine)
    chats = {
        "ONE": conversations.create(character_id, scenario_id=scenario.id).id,
        "TWO": conversations.create(character_id).id,
        "THREE": conversations.create(character_id).id,
    }
    messages = MessageService(engine)
    for marker, chat_id in chats.items():
        messages.send(chat_id, f"Messaggio-{marker}")

    runtime = RecordingRuntime()
    service = ChatInferenceService(engine, runtime, EventHub("boot"))
    scheduler = JobScheduler(engine, JobQueue(), {JobKind.CHAT_REPLY: service}, Leases(), lambda: RequiredMode.LLM)
    for _ in chats:
        finished = await scheduler.run_once()
        assert finished is not None
        assert finished.status is JobStatus.COMPLETED

    # The user message each reply answers tells which chat the prompt was built for.
    prompts = {
        marker: next(prompt for prompt in runtime.prompts if f"Messaggio-{marker}" in current_message(prompt))
        for marker in chats
    }
    for marker, prompt in prompts.items():
        for other in chats.keys() - {marker}:
            assert f"Messaggio-{other}" not in prompt
    # Only the chat that started from the scenario carries its text, in the system part and as the first turn.
    assert "Descrizione-ALPHA" in prompts["ONE"]
    assert "Apertura-ALPHA" in prompts["ONE"]
    assert "ALPHA" not in prompts["TWO"]
    assert "ALPHA" not in prompts["THREE"]


def current_message(prompt: str) -> str:
    """Returns the user message a `gemma4` reply prompt ends with: the text after its last message marker."""
    return prompt.rsplit("[Messaggio di Utente]\n", 1)[1].split("<turn|>", 1)[0]
