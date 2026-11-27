"""T-CRD-08 (P10.5b): a card exported by Lyrveil imports back from its embedded Lyrveil card, a card whose text was
edited elsewhere names the changed fields, and the embedded card passes the card rules whatever its hash says, through
the API on a real database."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine

from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"
CARD = {
    "identity": {"first_name": "Nerea", "last_name": "Falchi", "age": 27, "occupation": "Cartografa"},
    "traits": [
        {"category": "main_trait", "description": "Ironica e precisa."},
        {"category": "flaw", "description": "Testarda."},
    ],
    "communication": {"examples": [{"user": "Ciao, come va?", "character": "Meglio ora che sei qui."}]},
    "visual": {"hair": "Capelli corti e scuri."},
}


@pytest.fixture
def client(settings: Settings, engine: Engine, tmp_path: Path) -> TestClient:
    upgrade_to_head(engine)
    app = create_app(settings, engine, media_store=MediaStore(tmp_path / "data" / "media"))
    return TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)


def create_character(client: TestClient) -> str:
    response = client.post(CHARACTERS, json=CARD)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def exported_v3(client: TestClient, character_id: str) -> dict[str, Any]:
    response = client.get(f"{CHARACTERS}/{character_id}/card")
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    return document


def import_json(client: TestClient, document: dict[str, Any]) -> Any:
    content = json.dumps(document).encode("utf-8")
    return client.post(f"{CHARACTERS}/imports", files={"file": ("card.json", content, "application/json")})


def embedded_card_of(client: TestClient, character_id: str) -> dict[str, Any]:
    card: dict[str, Any] = exported_v3(client, character_id)["data"]["extensions"]["lyrveil"]["card"]
    return card


def test_lyrveil_to_v3_png_to_lyrveil_gives_an_equal_card(client: TestClient) -> None:
    character_id = create_character(client)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 80, 40)).save(buffer, "PNG")
    files = {"file": ("a.png", buffer.getvalue(), "image/png")}
    assert client.post(f"{CHARACTERS}/{character_id}/assets", files=files, data={"kind": "avatar"}).status_code == 201
    png = client.get(f"{CHARACTERS}/{character_id}/card", params={"format": "png"})
    assert png.status_code == 200, png.text

    imported = client.post(f"{CHARACTERS}/imports", files={"file": ("nerea.png", png.content, "image/png")})
    assert imported.status_code == 201, imported.text
    embedded = imported.json()["embedded_lyrveil"]
    assert (embedded["text_matches"], embedded["changed_fields"]) == (True, [])

    saved = client.post(CHARACTERS, json={**embedded["card"], "import_id": imported.json()["id"]})
    assert saved.status_code == 201, saved.text
    assert embedded_card_of(client, saved.json()["id"]) == embedded_card_of(client, character_id)


def test_a_card_edited_elsewhere_names_its_changed_fields_and_keeps_the_direct_mapping(client: TestClient) -> None:
    document = exported_v3(client, create_character(client))
    document["data"]["description"] += "\n\nHa una cicatrice sul mento."
    document["data"]["mes_example"] = "<START>\n{{user}}: Ciao.\n{{char}}: Salve."

    imported = import_json(client, document)
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["embedded_lyrveil"]["text_matches"] is False
    assert body["embedded_lyrveil"]["changed_fields"] == ["description", "mes_example"]
    assert body["embedded_lyrveil"]["card"]["identity"]["first_name"] == "Nerea"
    # **Usa i testi modificati** opens the direct mapping, which reads the edited text.
    assert "cicatrice" in body["direct_draft"]["draft"]["identity"]["biography"]


def test_a_card_without_an_embedded_lyrveil_card_has_none(client: TestClient) -> None:
    document = exported_v3(client, create_character(client))
    del document["data"]["extensions"]["lyrveil"]
    assert import_json(client, document).json()["embedded_lyrveil"] is None


def test_a_tampered_embedded_card_with_a_matching_hash_cannot_bypass_a_card_rule(client: TestClient) -> None:
    document = exported_v3(client, create_character(client))
    document["data"]["extensions"]["lyrveil"]["card"]["identity"]["age"] = 15

    refused = import_json(client, document)
    assert (refused.status_code, refused.json()["code"]) == (422, "IMPORT_INVALID")
    assert "18" in refused.json()["detail"]


def test_an_embedded_card_the_rules_refuse_is_ignored_when_the_text_was_edited(client: TestClient) -> None:
    document = exported_v3(client, create_character(client))
    document["data"]["extensions"]["lyrveil"]["card"]["identity"]["age"] = 15
    document["data"]["description"] = "Un'altra descrizione."

    imported = import_json(client, document)
    assert imported.status_code == 201, imported.text
    assert imported.json()["embedded_lyrveil"] is None


def test_a_later_read_of_the_import_gives_the_same_embedded_card(client: TestClient) -> None:
    document = exported_v3(client, create_character(client))
    imported = import_json(client, document).json()
    again = client.get(f"{CHARACTERS}/imports/{imported['id']}")
    assert again.status_code == 200
    assert again.json()["embedded_lyrveil"] == imported["embedded_lyrveil"]
