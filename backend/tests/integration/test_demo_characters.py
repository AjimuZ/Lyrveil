"""P03.6b: the original demo characters load on request, once, without touching user characters, and each one passes the
content guard, the card rules and the import validation, through the API on a real database."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from lyrveil.main import create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.card_rules import validate_card
from lyrveil.services.demo_characters import DEMO_CARDS
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    upgrade_to_head(engine)
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200", raise_server_exceptions=False)


def test_the_demo_cards_pass_the_card_rules_and_the_content_guard() -> None:
    for raw in DEMO_CARDS:
        validate_card(raw)


def test_the_demo_cards_cover_three_registers() -> None:
    registers = {
        (
            raw["communication"]["formality"],
            raw["communication"]["address_form"],
            raw["communication"]["message_length"],
        )
        for raw in DEMO_CARDS
    }
    assert len(DEMO_CARDS) == 3
    assert len(registers) == 3
    assert {raw["communication"]["address_form"] for raw in DEMO_CARDS} == {"tu", "lei"}


def test_a_load_adds_the_demo_characters_once(client: TestClient) -> None:
    first = client.post(f"{CHARACTERS}/demo")
    assert first.status_code == 201, first.text
    assert [item["first_name"] for item in first.json()["created"]] == ["Nerea", "Ilario", "Tessa"]
    assert first.json()["skipped"] == []

    second = client.post(f"{CHARACTERS}/demo")
    assert second.status_code == 201, second.text
    assert second.json()["created"] == []
    assert len(second.json()["skipped"]) == 3
    assert len(client.get(CHARACTERS).json()["items"]) == 3

    detail = client.get(f"{CHARACTERS}/{first.json()['created'][0]['id']}").json()
    assert detail["is_demo"] is True


def test_a_load_leaves_a_user_character_of_the_same_name_alone(client: TestClient) -> None:
    body: dict[str, Any] = {"identity": {"first_name": "Nerea", "last_name": "Falchi", "age": 40}}
    mine = client.post(CHARACTERS, json=body).json()["id"]
    assert client.post(f"{CHARACTERS}/demo").status_code == 201
    detail = client.get(f"{CHARACTERS}/{mine}").json()
    assert detail["is_demo"] is False
    assert detail["card"]["identity"]["age"] == 40
    assert detail["current_version_no"] == 1


def test_an_exported_demo_character_passes_the_import_validation(client: TestClient) -> None:
    created = client.post(f"{CHARACTERS}/demo").json()["created"]
    for item in created:
        document = client.get(f"{CHARACTERS}/{item['id']}/export", params={"include_assets": False}).json()
        data = json.dumps(document).encode()
        response = client.post(
            f"{CHARACTERS}/import", files={"file": ("c.lyrveil-character.json", data, "application/json")}
        )
        assert response.status_code == 201, response.text
