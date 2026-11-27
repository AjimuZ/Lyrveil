"""Checks the lore routes of docs/api.md and the copy of a scenario's links at a conversation's start (P12.1b).

Covers the API half of T-LOR-04 (an invalid entry is refused with its field and limit) and the link part of T-LOR-02: a
link has exactly one target, and a conversation that starts from a scenario keeps its own copy of the scenario's links.
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
MINOR_TEXT = "Una bambina di sette anni abita al faro."


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


def make_world(client: TestClient, name: str = "Aldara", **body: object) -> dict[str, Any]:
    response = client.post(f"{API}/lore/worlds", json={"name": name, **body})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def make_scenario(client: TestClient) -> dict[str, Any]:
    response = client.post(f"{API}/scenarios", json={"title": "Sera al faro"})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def links_of(client: TestClient, book_id: str) -> list[dict[str, Any]]:
    response = client.get(f"{API}/lore/books/{book_id}/links")
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def test_a_world_is_created_read_listed_replaced_and_deleted(client: TestClient) -> None:
    world = make_world(client, "Aldara", description="Un regno di mare.")
    assert (world["kind"], world["character_id"], world["scan_depth"], world["token_budget"]) == (
        "world",
        None,
        4,
        None,
    )
    assert (world["status"], world["entry_count"]) == ("active", 0)
    assert client.get(f"{API}/lore/books/{world['id']}").json() == world
    other = make_world(client, "Brume")
    assert [item["name"] for item in client.get(f"{API}/lore/worlds").json()["items"]] == ["Aldara", "Brume"]
    assert [item["id"] for item in client.get(f"{API}/lore/worlds", params={"q": "MARE"}).json()["items"]] == [
        world["id"]
    ]
    assert client.get(f"{API}/lore/worlds", params={"status": "archived"}).json() == {"items": []}

    replaced = client.put(f"{API}/lore/books/{world['id']}", json={"name": "Aldara II", "scan_depth": 20})
    assert replaced.status_code == 200
    assert (replaced.json()["name"], replaced.json()["scan_depth"], replaced.json()["description"]) == (
        "Aldara II",
        20,
        "",
    )

    assert client.delete(f"{API}/lore/books/{other['id']}").status_code == 204
    assert client.get(f"{API}/lore/books/{other['id']}").status_code == 404


def test_a_world_refuses_a_blank_name_a_value_out_of_range_and_a_minor(client: TestClient) -> None:
    for body in ({"name": "  "}, {"description": "senza nome"}, {"name": "Mondo", "scan_depth": 21}):
        refused = client.post(f"{API}/lore/worlds", json=body)
        assert (refused.status_code, refused.json()["code"]) == (422, "VALIDATION_ERROR"), body
    budget = client.post(f"{API}/lore/worlds", json={"name": "Mondo", "token_budget": 49})
    assert (budget.status_code, "50" in budget.json()["detail"]) == (422, True)
    minor = client.post(f"{API}/lore/worlds", json={"name": "Il regno delle bambine"})
    assert (minor.status_code, minor.json()["code"]) == (422, "CONTENT_POLICY_BLOCKED")
    assert client.get(f"{API}/lore/worlds").json() == {"items": []}


def test_unknown_books_entries_and_links_answer_404(client: TestClient) -> None:
    missing = new_id()
    calls = [
        ("GET", f"/lore/books/{missing}", None),
        ("PUT", f"/lore/books/{missing}", {"name": "x"}),
        ("DELETE", f"/lore/books/{missing}", None),
        ("GET", f"/characters/{missing}/lore", None),
        ("GET", f"/lore/books/{missing}/entries", None),
        ("POST", f"/lore/books/{missing}/entries", {"content": "x"}),
        ("GET", f"/lore/entries/{missing}", None),
        ("PUT", f"/lore/entries/{missing}", {"content": "x"}),
        ("DELETE", f"/lore/entries/{missing}", None),
        ("GET", f"/lore/books/{missing}/links", None),
        ("POST", f"/lore/books/{missing}/links", {"character_id": missing}),
        ("DELETE", f"/lore/links/{missing}", None),
    ]
    for method, path, body in calls:
        response = client.request(method, f"{API}{path}", json=body)
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND"), (method, path)


def test_the_own_book_is_created_empty_on_first_use_and_cannot_be_deleted(client: TestClient, engine: Engine) -> None:
    character_id = add_character(engine, "Alba")
    first = client.get(f"{API}/characters/{character_id}/lore")
    assert first.status_code == 200
    book = first.json()
    assert (book["kind"], book["character_id"], book["name"]) == ("character", character_id, "Alba")
    assert (book["entry_count"], book["scan_depth"], book["status"]) == (0, 4, "active")
    assert client.get(f"{API}/characters/{character_id}/lore").json() == book

    refused = client.delete(f"{API}/lore/books/{book['id']}")
    assert (refused.status_code, refused.json()["code"]) == (409, "CONFLICT")
    assert client.get(f"{API}/lore/books/{book['id']}").status_code == 200
    assert client.get(f"{API}/lore/worlds").json() == {"items": []}


def test_an_entry_is_created_read_filtered_replaced_and_deleted(client: TestClient) -> None:
    world = make_world(client)
    created = client.post(
        f"{API}/lore/books/{world['id']}/entries",
        json={"title": "Il faro", "content": "Un faro bianco sulla scogliera.", "keys": ["faro", "scogliera"]},
    )
    assert created.status_code == 201, created.text
    entry = created.json()
    assert (entry["keys"], entry["enabled"], entry["priority"], entry["insertion_order"]) == (
        ["faro", "scogliera"],
        True,
        0,
        100,
    )
    assert entry["never_inserted"] is False
    off_body = {"content": "Una nota di porto.", "enabled": False, "insertion_order": 200}
    off = client.post(f"{API}/lore/books/{world['id']}/entries", json=off_body).json()

    assert client.get(f"{API}/lore/entries/{entry['id']}").json() == entry
    assert client.get(f"{API}/lore/books/{world['id']}").json()["entry_count"] == 2
    listed = client.get(f"{API}/lore/books/{world['id']}/entries")
    assert [item["id"] for item in listed.json()["items"]] == [entry["id"], off["id"]]
    enabled = client.get(f"{API}/lore/books/{world['id']}/entries", params={"enabled": "true"})
    assert [item["id"] for item in enabled.json()["items"]] == [entry["id"]]
    found = client.get(f"{API}/lore/books/{world['id']}/entries", params={"q": "SCOGLIERA"})
    assert [item["id"] for item in found.json()["items"]] == [entry["id"]]

    replaced = client.put(f"{API}/lore/entries/{entry['id']}", json={"content": "Spento.", "keys": ["notte"]})
    assert replaced.status_code == 200
    assert (replaced.json()["title"], replaced.json()["keys"]) == ("", ["notte"])

    assert client.delete(f"{API}/lore/entries/{entry['id']}").status_code == 204
    assert client.get(f"{API}/lore/entries/{entry['id']}").status_code == 404


def test_an_invalid_entry_is_refused_with_its_field_and_limit_and_nothing_is_stored(client: TestClient) -> None:
    world = make_world(client)
    path = f"{API}/lore/books/{world['id']}/entries"
    refused: list[tuple[dict[str, Any], str, str]] = [
        ({"content": "  "}, "contenuto", ""),
        ({"content": "x" * 8001}, "contenuto", "8000"),
        ({"content": "x", "title": "t" * 81}, "titolo", "80"),
        ({"content": "x", "note": "n" * 501}, "nota", "500"),
        ({"content": "x", "keys": [f"chiave{index}" for index in range(17)]}, "parole chiave", "16"),
        ({"content": "x", "keys": ["k" * 61]}, "parole chiave", "60"),
        ({"content": "x", "keys": ["lui"]}, "lui", ""),
        ({"content": "x", "priority": "alta"}, "priorità", "1000000"),
    ]
    for body, field, limit in refused:
        response = client.post(path, json=body)
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR"), body
        assert field in response.json()["detail"], body
        assert limit in response.json()["detail"], body
    for field in ("title", "content"):
        body = {"content": "x", "title": "y", field: MINOR_TEXT}
        minor = client.post(path, json=body)
        assert (minor.status_code, minor.json()["code"]) == (422, "CONTENT_POLICY_BLOCKED"), field
    assert client.get(path).json() == {"items": []}


def test_a_link_has_exactly_one_target_and_only_a_world_is_linked(client: TestClient, engine: Engine) -> None:
    world = make_world(client)
    path = f"{API}/lore/books/{world['id']}/links"
    character_id = add_character(engine)
    scenario = make_scenario(client)
    conversation = client.post(f"{API}/conversations", json={"character_id": character_id}).json()

    targets = [{"character_id": character_id}, {"scenario_id": scenario["id"]}, {"conversation_id": conversation["id"]}]
    for target in targets:
        created = client.post(path, json=target)
        assert created.status_code == 201, created.text
        link = created.json()
        assert link["book_id"] == world["id"]
        assert {key: link[key] for key in target} == target
        assert sum(link[key] is not None for key in ("character_id", "scenario_id", "conversation_id")) == 1
    assert len(links_of(client, world["id"])) == 3

    for body in ({}, {"character_id": character_id, "scenario_id": scenario["id"]}):
        both = client.post(path, json=body)
        assert (both.status_code, both.json()["code"]) == (422, "VALIDATION_ERROR"), body
    duplicate = client.post(path, json={"character_id": character_id})
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "CONFLICT")
    for target in ({"character_id": new_id()}, {"scenario_id": new_id()}, {"conversation_id": new_id()}):
        unknown = client.post(path, json=target)
        assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND"), target

    own = client.get(f"{API}/characters/{character_id}/lore").json()
    refused = client.post(f"{API}/lore/books/{own['id']}/links", json={"character_id": character_id})
    assert (refused.status_code, refused.json()["code"]) == (409, "CONFLICT")
    assert links_of(client, own["id"]) == []
    assert len(links_of(client, world["id"])) == 3


def test_a_deleted_link_leaves_the_world_and_its_target(client: TestClient, engine: Engine) -> None:
    world = make_world(client)
    character_id = add_character(engine)
    link = client.post(f"{API}/lore/books/{world['id']}/links", json={"character_id": character_id}).json()
    assert client.delete(f"{API}/lore/links/{link['id']}").status_code == 204
    assert client.delete(f"{API}/lore/links/{link['id']}").status_code == 404
    assert links_of(client, world["id"]) == []
    assert client.get(f"{API}/lore/books/{world['id']}").status_code == 200
    assert client.post(f"{API}/lore/books/{world['id']}/links", json={"character_id": character_id}).status_code == 201


def test_a_conversation_copies_its_scenarios_links_and_later_scenario_changes_do_not_reach_it(
    client: TestClient, engine: Engine
) -> None:
    character_id = add_character(engine)
    scenario = make_scenario(client)
    kept = make_world(client, "Aldara")
    removed = make_world(client, "Brume")
    added = make_world(client, "Cenere")
    scenario_links = {
        world["id"]: client.post(f"{API}/lore/books/{world['id']}/links", json={"scenario_id": scenario["id"]}).json()
        for world in (kept, removed)
    }

    started = client.post(f"{API}/conversations", json={"character_id": character_id, "scenario_id": scenario["id"]})
    assert started.status_code == 201, started.text
    conversation_id = started.json()["id"]
    for world in (kept, removed):
        copies = [link for link in links_of(client, world["id"]) if link["conversation_id"] is not None]
        assert [link["conversation_id"] for link in copies] == [conversation_id]
        assert copies[0]["id"] != scenario_links[world["id"]]["id"]
        assert (copies[0]["scenario_id"], copies[0]["character_id"]) == (None, None)

    assert client.delete(f"{API}/lore/links/{scenario_links[removed['id']]['id']}").status_code == 204
    assert client.post(f"{API}/lore/books/{added['id']}/links", json={"scenario_id": scenario["id"]}).status_code == 201

    def conversation_worlds(conversation: str) -> list[str]:
        return sorted(
            world["id"]
            for world in (kept, removed, added)
            if any(link["conversation_id"] == conversation for link in links_of(client, world["id"]))
        )

    assert conversation_worlds(conversation_id) == sorted([kept["id"], removed["id"]])
    later = client.post(f"{API}/conversations", json={"character_id": character_id, "scenario_id": scenario["id"]})
    assert conversation_worlds(later.json()["id"]) == sorted([kept["id"], added["id"]])

    plain = client.post(f"{API}/conversations", json={"character_id": character_id}).json()
    assert conversation_worlds(plain["id"]) == []


def test_deleting_a_scenario_keeps_the_links_its_conversations_copied(client: TestClient, engine: Engine) -> None:
    character_id = add_character(engine)
    scenario = make_scenario(client)
    world = make_world(client)
    client.post(f"{API}/lore/books/{world['id']}/links", json={"scenario_id": scenario["id"]})
    conversation = client.post(
        f"{API}/conversations", json={"character_id": character_id, "scenario_id": scenario["id"]}
    ).json()
    assert client.delete(f"{API}/scenarios/{scenario['id']}").status_code == 204
    assert [link["conversation_id"] for link in links_of(client, world["id"])] == [conversation["id"]]


def test_match_answers_each_entry_with_its_key_or_its_reason(client: TestClient) -> None:
    world = make_world(client)
    ids = {}
    for name, body in {
        "lupo": {"title": "Lupo", "content": "Un lupo.", "keys": ["lupo"]},
        "sogno": {"content": "Un faro.", "keys": ["faro"], "exclude_keys": ["sogno"]},
        "spento": {"content": "Spento.", "keys": ["faro"], "enabled": False},
        "sempre": {"content": "Sempre.", "constant": True},
        "nave": {"content": "Una nave.", "keys": ["nave"]},
    }.items():
        response = client.post(f"{API}/lore/books/{world['id']}/entries", json=body)
        assert response.status_code == 201, response.text
        ids[name] = response.json()["id"]

    response = client.post(
        f"{API}/lore/books/{world['id']}/match", json={"text": "Nel sogno vidi i lupi e un faro.", "language": "it"}
    )

    assert response.status_code == 200, response.text
    found = {item["entry_id"]: item for item in response.json()["items"]}
    assert len(found) == 5
    assert (found[ids["lupo"]]["active"], found[ids["lupo"]]["matched_key"]) == (True, "lupo")
    assert (found[ids["sogno"]]["reason"], found[ids["sogno"]]["excluded_key"]) == ("excluded", "sogno")
    assert found[ids["spento"]]["reason"] == "disabled"
    assert found[ids["sempre"]]["reason"] == "constant"
    assert found[ids["nave"]]["reason"] == "no_key_matched"


def test_match_refuses_a_missing_text_and_an_unknown_book(client: TestClient) -> None:
    world = make_world(client)
    url = f"{API}/lore/books/{world['id']}/match"

    assert client.post(url, json={"language": "it"}).status_code == 422
    assert client.post(url, json={"text": "x" * 20_001}).status_code == 422
    assert client.post(f"{API}/lore/books/{new_id()}/match", json={"text": "x"}).status_code == 404
