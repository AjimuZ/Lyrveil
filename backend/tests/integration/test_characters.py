"""Checks the minimal character routes of P02.6a through the API on a real database."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from lyrveil.main import create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

URL = "/api/v1/characters"


@pytest.fixture
def client(settings: Settings, engine: Engine) -> TestClient:
    upgrade_to_head(engine)
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


def test_a_character_is_created_trimmed_and_listed(client: TestClient) -> None:
    response = client.post(URL, json={"first_name": "  Alba ", "last_name": " ", "age": 30})
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["first_name"], created["last_name"], created["full_name"]) == ("Alba", None, "Alba")
    assert (created["grammatical_gender"], created["primary_language"], created["status"]) == (
        "non_indicato",
        "it",
        "active",
    )
    assert client.get(URL).json()["items"] == [created]


def test_a_new_character_can_start_a_chat(client: TestClient) -> None:
    created = client.post(URL, json={"first_name": "Alba", "last_name": "Rossi", "age": 41}).json()
    chat = client.post("/api/v1/conversations", json={"character_id": created["id"]})
    assert chat.status_code == 201
    assert chat.json()["title"] == "Alba Rossi"


def test_the_service_refuses_invalid_values_with_an_italian_detail(client: TestClient) -> None:
    for body, detail in (
        ({"first_name": " ", "age": 30}, "Il nome deve avere da 1 a 80 caratteri."),
        ({"first_name": "Alba", "age": 17}, "L'età deve essere almeno 18."),
        ({"first_name": "Alba", "age": 30, "grammatical_gender": "x"}, "Il genere grammaticale non è valido."),
        ({"first_name": "Alba", "age": 30, "primary_language": "xx"}, "La lingua non è supportata."),
    ):
        response = client.post(URL, json=body)
        assert (response.status_code, response.json()["code"], response.json()["detail"]) == (
            422,
            "VALIDATION_ERROR",
            detail,
        )
    assert client.get(URL).json()["items"] == []


def test_the_list_filters_by_name_and_status(client: TestClient) -> None:
    client.post(URL, json={"first_name": "Alba", "last_name": "Rossi", "age": 30})
    client.post(URL, json={"first_name": "Marco", "age": 30})

    def names(**query: str) -> list[str]:
        return [item["full_name"] for item in client.get(URL, params=query).json()["items"]]

    assert sorted(names()) == ["Alba Rossi", "Marco"]
    assert names(q="ross") == ["Alba Rossi"]
    assert names(q="%") == []
    assert names(status="archived") == []
    assert client.get(URL, params={"status": "deleted"}).status_code == 422
