"""T-CRD-07 (P10.5a): `GET /characters/{id}/card` writes V3 JSON, V2 JSON and a PNG with both card chunks, renders the
free text deterministically, embeds the native card in `extensions.lyrveil`, keeps the source fields of an imported
character and holds nothing else of the character, through the API on a real database."""

import base64
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import Engine

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.tavern_card import CardChunk, CardContainer, CardSpec, parse_card
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.card_export import render_texts, text_sha256
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"
SCENARIOS = "/api/v1/scenarios"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tavern_cards"
V3_REQUIRED = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "creator_notes",
    "system_prompt",
    "post_history_instructions",
    "alternate_greetings",
    "tags",
    "creator",
    "character_version",
    "extensions",
    "group_only_greetings",
    "creation_date",
    "modification_date",
)
CARD = {
    "identity": {"first_name": "Nerea", "last_name": "Falchi", "age": 27, "occupation": "Cartografa"},
    "traits": [
        {"category": "main_trait", "description": "Ironica e precisa."},
        {"category": "flaw", "description": "Testarda."},
        {"category": "flaw", "description": "Impaziente."},
    ],
    "communication": {
        "examples": [
            {"user": "Ciao, come va?", "character": "Meglio ora che sei qui."},
            {"character": "Parlo da sola, ogni tanto."},
        ]
    },
    "visual": {"hair": "Capelli corti e scuri."},
}


@pytest.fixture
def client(settings: Settings, engine: Engine, tmp_path: Path) -> TestClient:
    upgrade_to_head(engine)
    app = create_app(settings, engine, media_store=MediaStore(tmp_path / "data" / "media"))
    return TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 80, 40)).save(buffer, "PNG")
    return buffer.getvalue()


def create_character(client: TestClient) -> str:
    response = client.post(CHARACTERS, json=CARD)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def add_avatar(client: TestClient, character_id: str) -> None:
    files = {"file": ("a.png", png_bytes(), "image/png")}
    response = client.post(f"{CHARACTERS}/{character_id}/assets", files=files, data={"kind": "avatar"})
    assert response.status_code == 201, response.text


def add_scenario(client: TestClient, character_id: str | None, title: str, opening: str) -> str:
    body = {
        "title": title,
        "description": f"{title}: descrizione.",
        "opening_message": opening,
        "character_id": character_id,
    }
    response = client.post(SCENARIOS, json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def export(client: TestClient, character_id: str, **params: Any) -> Any:
    return client.get(f"{CHARACTERS}/{character_id}/card", params=params)


def png_chunks(content: bytes) -> dict[str, dict[str, Any]]:
    """The `tEXt` chunks of a PNG, decoded from base64 JSON, by keyword."""
    image = Image.open(io.BytesIO(content))
    assert isinstance(image, PngImagePlugin.PngImageFile)
    return {key: json.loads(base64.b64decode(value)) for key, value in image.text.items()}


def test_v3_export_has_every_required_field_and_the_native_card(client: TestClient) -> None:
    character_id = create_character(client)
    first = add_scenario(client, character_id, "Officina", "*Alza lo sguardo.* Sei in ritardo.")
    second = add_scenario(client, None, "Porto", "Il porto è silenzioso.")
    response = export(client, character_id, scenario_ids=f"{first},{second}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == 'attachment; filename="nerea.json"'

    document = response.json()
    assert (document["spec"], document["spec_version"]) == ("chara_card_v3", "3.0")
    data = document["data"]
    assert all(key in data for key in V3_REQUIRED)
    assert data["name"] == "Nerea Falchi"
    assert "Nerea Falchi, 27 anni." in data["description"]
    assert "Aspetto:\nCapelli: Capelli corti e scuri." in data["description"]
    assert data["personality"] == "Carattere: Ironica e precisa.\nDifetti: Testarda.; Impaziente."
    assert data["mes_example"] == (
        "<START>\n{{user}}: Ciao, come va?\n{{char}}: Meglio ora che sei qui.\n"
        "<START>\n{{char}}: Parlo da sola, ogni tanto."
    )
    assert data["scenario"] == "Officina: descrizione."
    assert data["first_mes"] == "*Alza lo sguardo.* Sei in ritardo."
    assert data["alternate_greetings"] == ["Il porto è silenzioso."]
    assert (data["system_prompt"], data["post_history_instructions"], data["group_only_greetings"]) == ("", "", [])
    assert "character_book" not in data

    detail = client.get(f"{CHARACTERS}/{character_id}").json()
    lyrveil = data["extensions"]["lyrveil"]
    assert lyrveil["format"] == "lyrveil.character"
    assert lyrveil["format_version"] == 1
    assert lyrveil["card"] == detail["card"]
    assert CharacterCard.model_validate(lyrveil["card"]).identity.first_name == "Nerea"
    assert lyrveil["text_sha256"] == text_sha256(render_texts(CharacterCard.model_validate(detail["card"])))

    # No source: empty creator, the version number and the creation time of the character.
    assert (data["creator"], data["tags"], data["creator_notes"], data["source"]) == ("", [], "", [])
    assert data["character_version"] == "1"
    created = datetime.fromisoformat(detail["created_at"])
    assert data["creation_date"] == int(created.timestamp())
    assert isinstance(data["modification_date"], int)


def test_two_exports_of_an_unchanged_card_are_equal_apart_from_the_modification_date(client: TestClient) -> None:
    character_id = create_character(client)
    scenario = add_scenario(client, character_id, "Officina", "Ciao.")
    runs = [export(client, character_id, scenario_ids=scenario).json() for _ in range(2)]
    for run in runs:
        del run["data"]["modification_date"]
    assert runs[0] == runs[1]


def test_v2_export_has_the_v2_envelope_and_none_of_the_v3_fields(client: TestClient) -> None:
    character_id = create_character(client)
    response = export(client, character_id, format="v2json")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="nerea.v2.json"'
    document = response.json()
    assert (document["spec"], document["spec_version"]) == ("chara_card_v2", "2.0")
    assert set(document["data"]) == {
        "name",
        "description",
        "personality",
        "scenario",
        "first_mes",
        "mes_example",
        "creator_notes",
        "system_prompt",
        "post_history_instructions",
        "alternate_greetings",
        "tags",
        "creator",
        "character_version",
        "extensions",
    }
    assert document["data"]["extensions"]["lyrveil"]["card"]["identity"]["first_name"] == "Nerea"


def test_png_export_carries_both_chunks_and_the_card_reads_back(client: TestClient) -> None:
    character_id = create_character(client)
    add_avatar(client, character_id)
    response = export(client, character_id, format="png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'attachment; filename="nerea.png"'

    chunks = png_chunks(response.content)
    assert set(chunks) == {"ccv3", "chara"}
    assert (chunks["ccv3"]["spec"], chunks["chara"]["spec"]) == ("chara_card_v3", "chara_card_v2")
    assert chunks["ccv3"]["data"]["name"] == chunks["chara"]["data"]["name"] == "Nerea Falchi"

    parsed = parse_card(response.content)
    assert (parsed.spec, parsed.container, parsed.chunk) == (CardSpec.V3, CardContainer.PNG, CardChunk.CCV3)
    assert Image.open(io.BytesIO(response.content)).size == (8, 8)


def test_a_character_without_an_avatar_gets_a_clear_error_for_png_and_still_exports_json(client: TestClient) -> None:
    character_id = create_character(client)
    refused = export(client, character_id, format="png")
    assert (refused.status_code, refused.json()["code"]) == (409, "CONFLICT")
    assert "avatar" in refused.json()["detail"]
    assert export(client, character_id).status_code == 200


def test_an_unknown_character_format_or_scenario_is_refused(client: TestClient) -> None:
    character_id = create_character(client)
    other = create_character(client)
    foreign = add_scenario(client, other, "Altrove", "Ciao.")
    assert export(client, "missing").status_code == 404
    assert export(client, character_id, format="charx").status_code == 422
    assert export(client, character_id, scenario_ids="missing").status_code == 404
    assert export(client, character_id, scenario_ids=foreign).status_code == 404


def test_an_imported_character_keeps_its_source_fields_and_unknown_extensions(client: TestClient) -> None:
    source = json.loads((FIXTURES / "v3.json").read_text(encoding="utf-8"))
    imported = client.post(
        f"{CHARACTERS}/imports", files={"file": ("card.json", json.dumps(source).encode("utf-8"), "application/json")}
    ).json()
    draft = imported["direct_draft"]["draft"]
    body = {
        "identity": draft["identity"],
        "traits": draft["traits"],
        "communication": draft["communication"],
        "import_id": imported["id"],
    }
    saved = client.post(CHARACTERS, json=body)
    assert saved.status_code == 201, saved.text
    character_id = saved.json()["id"]

    data = export(client, character_id).json()["data"]
    original = source["data"]
    assert data["tags"] == original["tags"]
    assert data["creator"] == original["creator"]
    assert data["creator_notes"] == original["creator_notes"]
    assert data["source"] == original["source"]
    assert data["character_version"] == original["character_version"]
    assert data["creation_date"] == original["creation_date"]
    assert data["extensions"]["example_tool"] == original["extensions"]["example_tool"]
    assert "lyrveil" in data["extensions"]
    # What the card read but Lyrveil never uses stays out of the export.
    assert (data["system_prompt"], data["group_only_greetings"]) == ("", [])

    # A card, then Lyrveil, then a card: the exported PNG still holds the unknown extension.
    add_avatar(client, character_id)
    again = parse_card(export(client, character_id, format="png").content)
    assert again.data.extensions["example_tool"] == original["extensions"]["example_tool"]
