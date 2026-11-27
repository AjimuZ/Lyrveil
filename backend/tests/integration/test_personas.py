"""Checks the persona routes and the persona of a conversation through the API on a real database (P11.1b).

Covers the API half of T-PSN-02 (lengths, age, content guard, one default, the archive and delete rules) and the
conversation half of T-PSN-04 (`persona_id` is fixed at creation, `persona_note` can be edited).
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from lyrveil.domain.identifiers import new_id
from lyrveil.jobs.scheduler import write
from lyrveil.main import create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

NOW = "2026-11-27T10:00:00.000Z"
API = "/api/v1"


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    upgrade_to_head(engine)
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


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


def make_persona(client: TestClient, **body: object) -> dict[str, Any]:
    response = client.post(f"{API}/personas", json={"name": "Marco", **body})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def start_chat(client: TestClient, character_id: str, **body: object) -> dict[str, Any]:
    response = client.post(f"{API}/conversations", json={"character_id": character_id, **body})
    assert response.status_code == 201, response.text
    started: dict[str, Any] = response.json()
    return started


def default_persona(client: TestClient) -> dict[str, Any]:
    items = client.get(f"{API}/personas").json()["items"]
    defaults = [item for item in items if item["is_default"]]
    assert len(defaults) == 1
    found: dict[str, Any] = defaults[0]
    return found


def test_the_migration_persona_is_the_only_default_and_new_ones_are_not(client: TestClient) -> None:
    first = default_persona(client)
    assert (first["name"], first["grammatical_gender"], first["status"]) == ("Utente", "non_indicato", "active")
    created = make_persona(client, grammatical_gender="maschile", age=34, appearance="Alto", about="Fa il cuoco")
    assert (created["is_default"], created["conversation_count"], created["age"]) == (False, 0, 34)
    assert client.get(f"{API}/personas/{created['id']}").json() == created
    assert [item["id"] for item in client.get(f"{API}/personas").json()["items"]] == [first["id"], created["id"]]


def test_a_persona_refuses_a_bad_name_age_gender_and_a_text_about_a_minor(client: TestClient) -> None:
    refused_validation = [
        {"name": "   "},
        {"name": "x" * 41},
        {"name": "Marco", "age": 17},
        {"name": "Marco", "age": 121},
        {"name": "Marco", "grammatical_gender": "altro"},
        {"name": "Marco", "appearance": "x" * 601},
        {"name": "Marco", "about": "x" * 1201},
    ]
    for body in refused_validation:
        response = client.post(f"{API}/personas", json=body)
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR"), body
    for body in ({"name": "x" * 40}, {"name": "Marco", "age": 18}, {"name": "Luca", "age": 120}, {"name": "Ugo"}):
        assert client.post(f"{API}/personas", json=body).status_code == 201, body

    for field, label in (("appearance", "aspetto"), ("about", "chi sono")):
        minor = client.post(f"{API}/personas", json={"name": "Sara", field: "Una ragazza di 15 anni."})
        assert (minor.status_code, minor.json()["code"]) == (422, "CONTENT_POLICY_BLOCKED"), field
        assert label in minor.json()["detail"]
    assert len(client.get(f"{API}/personas").json()["items"]) == 5

    persona = make_persona(client, name="Elio")
    edited = client.put(f"{API}/personas/{persona['id']}", json={"name": "Elia", "age": 17})
    assert edited.status_code == 422
    minor_edit = client.put(
        f"{API}/personas/{persona['id']}", json={"name": "Elia", "about": "Una ragazza di 15 anni."}
    )
    assert (minor_edit.status_code, minor_edit.json()["code"]) == (422, "CONTENT_POLICY_BLOCKED")
    assert client.get(f"{API}/personas/{persona['id']}").json()["name"] == "Elio"


def test_a_persona_is_replaced_by_put_and_unknown_ids_are_404(client: TestClient) -> None:
    persona = make_persona(client, age=40, about="Fa il cuoco")
    replaced = client.put(
        f"{API}/personas/{persona['id']}", json={"name": "  Elia  ", "grammatical_gender": "femminile"}
    )
    assert replaced.status_code == 200
    body = replaced.json()
    assert (body["name"], body["grammatical_gender"], body["age"], body["about"]) == ("Elia", "femminile", None, "")
    for method, path in (
        ("get", ""),
        ("put", ""),
        ("delete", ""),
        ("post", "/default"),
        ("post", "/archive"),
        ("post", "/unarchive"),
    ):
        kwargs = {"json": {"name": "Elia"}} if method == "put" else {}
        response = getattr(client, method)(f"{API}/personas/{new_id()}{path}", **kwargs)
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND"), (method, path)


def test_exactly_one_persona_is_the_default_and_the_default_cannot_be_archived_or_deleted(client: TestClient) -> None:
    first = default_persona(client)
    second = make_persona(client, name="Giulia")
    assert client.post(f"{API}/personas/{first['id']}/archive").status_code == 409
    assert client.delete(f"{API}/personas/{first['id']}").status_code == 409
    assert client.post(f"{API}/personas/{first['id']}/default").json()["is_default"] is True

    moved = client.post(f"{API}/personas/{second['id']}/default").json()
    assert moved["is_default"] is True
    assert default_persona(client)["id"] == second["id"]
    assert client.get(f"{API}/personas/{first['id']}").json()["is_default"] is False
    assert client.post(f"{API}/personas/{second['id']}/archive").status_code == 409
    assert client.post(f"{API}/personas/{first['id']}/archive").json()["status"] == "archived"

    archived_default = client.post(f"{API}/personas/{first['id']}/default")
    assert (archived_default.status_code, archived_default.json()["code"]) == (409, "CONFLICT")
    assert default_persona(client)["id"] == second["id"]


def test_a_used_persona_can_only_be_archived_and_an_unused_one_can_be_deleted(
    client: TestClient, engine: Engine
) -> None:
    character_id = add_character(engine)
    used, unused = make_persona(client, name="Usata"), make_persona(client, name="Libera")
    chat = start_chat(client, character_id, persona_id=used["id"])
    assert client.get(f"{API}/personas/{used['id']}").json()["conversation_count"] == 1

    blocked = client.delete(f"{API}/personas/{used['id']}")
    assert (blocked.status_code, blocked.json()["code"]) == (409, "CONFLICT")
    assert client.get(f"{API}/personas/{used['id']}").status_code == 200

    assert client.delete(f"{API}/personas/{unused['id']}").status_code == 204
    assert client.get(f"{API}/personas/{unused['id']}").status_code == 404

    # A deleted conversation that can still be restored keeps its persona in use.
    assert client.delete(f"{API}/conversations/{chat['id']}").status_code == 204
    assert client.delete(f"{API}/personas/{used['id']}").status_code == 409


def test_archive_and_unarchive_move_a_persona_between_the_lists(client: TestClient) -> None:
    persona = make_persona(client)

    def ids(status: str) -> list[str]:
        response = client.get(f"{API}/personas", params={"status": status})
        return [item["id"] for item in response.json()["items"]]

    assert persona["id"] in ids("active")
    assert client.post(f"{API}/personas/{persona['id']}/archive").json()["status"] == "archived"
    assert persona["id"] not in ids("active")
    assert ids("archived") == [persona["id"]]
    assert client.post(f"{API}/personas/{persona['id']}/unarchive").json()["status"] == "active"
    assert ids("archived") == []


def test_a_new_conversation_takes_the_requested_persona_else_the_scenarios_else_the_default(
    client: TestClient, engine: Engine
) -> None:
    character_id = add_character(engine)
    default = default_persona(client)
    chosen, scenic = make_persona(client, name="Scelta"), make_persona(client, name="Del copione")
    scenario = client.post(f"{API}/scenarios", json={"title": "Sera", "persona_id": scenic["id"]})
    assert scenario.status_code == 201
    assert scenario.json()["persona_id"] == scenic["id"]
    plain = client.post(f"{API}/scenarios", json={"title": "Senza persona"}).json()

    assert start_chat(client, character_id)["persona_id"] == default["id"]
    assert start_chat(client, character_id, scenario_id=plain["id"])["persona_id"] == default["id"]
    by_scenario = start_chat(client, character_id, scenario_id=scenario.json()["id"])
    assert (by_scenario["persona_id"], by_scenario["persona_name"]) == (scenic["id"], "Del copione")
    explicit = start_chat(client, character_id, scenario_id=scenario.json()["id"], persona_id=chosen["id"])
    assert explicit["persona_id"] == chosen["id"]
    assert client.get(f"{API}/conversations/{explicit['id']}").json()["persona_id"] == chosen["id"]

    unknown = client.post(f"{API}/conversations", json={"character_id": character_id, "persona_id": new_id()})
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")
    missing = client.post(f"{API}/scenarios", json={"title": "Sera", "persona_id": new_id()})
    assert missing.status_code == 404


def test_an_archived_persona_is_refused_for_new_conversations_and_keeps_its_existing_ones(
    client: TestClient, engine: Engine
) -> None:
    character_id = add_character(engine)
    persona = make_persona(client, name="Archiviata")
    scenario = client.post(f"{API}/scenarios", json={"title": "Sera", "persona_id": persona["id"]}).json()
    existing = start_chat(client, character_id, persona_id=persona["id"])
    assert client.post(f"{API}/personas/{persona['id']}/archive").status_code == 200

    refused = client.post(f"{API}/conversations", json={"character_id": character_id, "persona_id": persona["id"]})
    assert (refused.status_code, refused.json()["code"]) == (409, "CONFLICT")
    kept = client.get(f"{API}/conversations/{existing['id']}").json()
    assert (kept["persona_id"], kept["persona_name"]) == (persona["id"], "Archiviata")
    assert client.patch(f"{API}/conversations/{existing['id']}", json={"persona_note": "Colleghi"}).status_code == 200
    # The user did not pick the persona that only the scenario names, so the default takes over.
    assert start_chat(client, character_id, scenario_id=scenario["id"])["persona_id"] == default_persona(client)["id"]


def test_the_persona_of_a_conversation_never_changes_and_its_note_does(client: TestClient, engine: Engine) -> None:
    character_id = add_character(engine)
    first, second = make_persona(client, name="Prima"), make_persona(client, name="Seconda")
    chat = start_chat(client, character_id, persona_id=first["id"], persona_note="  Siete colleghi da due anni  ")
    assert (chat["persona_id"], chat["persona_note"]) == (first["id"], "Siete colleghi da due anni")
    assert start_chat(client, character_id)["persona_note"] == ""

    for body in ({"persona_id": second["id"]}, {"persona_id": first["id"]}, {"persona_id": None}):
        refused = client.patch(f"{API}/conversations/{chat['id']}", json=body)
        assert (refused.status_code, refused.json()["code"]) == (422, "VALIDATION_ERROR"), body
    assert client.get(f"{API}/conversations/{chat['id']}").json()["persona_id"] == first["id"]

    edited = client.patch(f"{API}/conversations/{chat['id']}", json={"persona_note": "Vi siete conosciuti ieri"})
    assert (edited.status_code, edited.json()["persona_note"]) == (200, "Vi siete conosciuti ieri")
    assert edited.json()["persona_id"] == first["id"]
    cleared = client.patch(f"{API}/conversations/{chat['id']}", json={"persona_note": ""})
    assert cleared.json()["persona_note"] == ""


def test_the_persona_note_takes_600_characters_and_the_content_guard(client: TestClient, engine: Engine) -> None:
    character_id = add_character(engine)
    assert start_chat(client, character_id, persona_note="x" * 600)["persona_note"] == "x" * 600
    too_long = client.post(f"{API}/conversations", json={"character_id": character_id, "persona_note": "x" * 601})
    assert (too_long.status_code, too_long.json()["code"]) == (422, "VALIDATION_ERROR")
    minor = client.post(
        f"{API}/conversations", json={"character_id": character_id, "persona_note": "Una ragazza di 15 anni."}
    )
    assert (minor.status_code, minor.json()["code"]) == (422, "CONTENT_POLICY_BLOCKED")

    chat = start_chat(client, character_id)
    over = client.patch(f"{API}/conversations/{chat['id']}", json={"persona_note": "x" * 601})
    assert (over.status_code, over.json()["code"]) == (422, "VALIDATION_ERROR")
    assert client.get(f"{API}/conversations/{chat['id']}").json()["persona_note"] == ""
