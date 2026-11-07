"""T-CHR-05, T-CHR-06 and T-CHR-14 (round trip), and T-CHR-02 at import: the exchange file of a character, its export
with the assets and their hashes, the import that always creates a new character, and the refusals, through the API on a
real database."""

import base64
import copy
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine

from lyrveil.domain.errors import PayloadTooLargeError
from lyrveil.domain.exchange import MAX_IMPORT_BYTES, ExchangeFile, json_schema
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.character_exchange import CharacterExchangeService
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"

CARD: dict[str, Any] = {
    "identity": {
        "first_name": "Nerea",
        "last_name": "Falchi",
        "age": 27,
        "grammatical_gender": "femminile",
        "narrative_identity": "Cartografa di una città portuale.",
        "interests": ["mappe antiche", "gatti di porto"],
        "primary_language": "it",
    },
    "traits": [
        {"category": "main_trait", "description": "Ironica, curiosa.", "locked": False},
        {"category": "fear", "description": "Perdersi in un posto noto.", "locked": True},
    ],
    "communication": {
        "formality": "informale",
        "message_length": "brevi",
        "address_form": "lei",
        "narration": "leggera",
        "emoji": "mai",
        "examples": [
            {"user": "La mappa è pronta?", "character": "ok, ammetto che era al contrario."},
            {"user": None, "character": "portami un caffè"},
        ],
        "recurring_expressions": ["ma solo un po'"],
        "avoid": ["Che bella domanda!", "Sono qui per te"],
    },
    "visual": {
        "hair": "Capelli rossi corti.",
        "subject_tag": "1girl",
        "identity_en": {
            "hair_color": "red hair",
            "hair_length": "short hair",
            "eye_color": "green eyes",
            "hairstyle": ["messy hair"],
            "extra": ["brass compass pendant"],
        },
        "usual_outfit_en": ["canvas jacket", "many pockets"],
        "outfits": [
            {
                "key": "festa",
                "label": "Festa del porto",
                "description": "Abito blu scuro.",
                "tags_en": ["dark blue dress", "white ribbons"],
            }
        ],
        "locked_fields": ["hair", "eyes"],
        "seed_policy": "base_offset",
        "base_seed": 7,
    },
}


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "media"


@pytest.fixture
def client(settings: Settings, engine: Engine, media_root: Path) -> TestClient:
    upgrade_to_head(engine)
    app = create_app(settings, engine, media_store=MediaStore(media_root))
    return TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)


def picture(image_format: str, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, image_format)
    return buffer.getvalue()


def create_character(client: TestClient) -> str:
    response = client.post(CHARACTERS, json=CARD)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def upload(client: TestClient, character_id: str, data: bytes, media_type: str, kind: str = "avatar") -> None:
    response = client.post(
        f"{CHARACTERS}/{character_id}/assets", files={"file": ("a", data, media_type)}, data={"kind": kind}
    )
    assert response.status_code == 201, response.text


def assets_of(client: TestClient, character_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get(f"{CHARACTERS}/{character_id}/assets").json()["items"]
    return items


def exported(client: TestClient, character_id: str, include_assets: bool = True) -> dict[str, Any]:
    response = client.get(f"{CHARACTERS}/{character_id}/export", params={"include_assets": include_assets})
    assert response.status_code == 200, response.text
    document: dict[str, Any] = response.json()
    return document


def import_document(client: TestClient, document: dict[str, Any] | bytes) -> Any:
    data = document if isinstance(document, bytes) else json.dumps(document).encode()
    return client.post(f"{CHARACTERS}/import", files={"file": ("c.lyrveil-character.json", data, "application/json")})


def character_count(client: TestClient) -> int:
    return len(client.get(CHARACTERS).json()["items"])


def test_a_round_trip_preserves_the_card_and_the_asset_hashes(client: TestClient) -> None:
    source = create_character(client)
    upload(client, source, picture("PNG", (200, 40, 40)), "image/png")
    upload(client, source, picture("JPEG", (10, 120, 200)), "image/jpeg")
    upload(client, source, picture("WEBP", (30, 160, 30)), "image/webp", kind="reference")

    document = exported(client, source)
    imported = import_document(client, document)
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert (body["assets_imported"], body["assets_without_data"]) == (3, 0)
    new_id = body["character"]["id"]
    assert new_id != source

    original_card = client.get(f"{CHARACTERS}/{source}").json()["card"]
    assert client.get(f"{CHARACTERS}/{new_id}").json()["card"] == original_card
    original = assets_of(client, source)
    copied = assets_of(client, new_id)
    assert sorted(item["sha256"] for item in copied) == sorted(item["sha256"] for item in original)
    assert {item["sha256"] for item in copied if item["is_primary"]} == {
        item["sha256"] for item in original if item["is_primary"]
    }
    assert sorted(item["kind"] for item in copied) == ["avatar", "avatar", "reference"]
    versions = client.get(f"{CHARACTERS}/{new_id}/versions").json()["items"]
    assert [(item["version_no"], item["actor"]) for item in versions] == [(1, "import")]
    # Exporting the copy again gives the same card and the same hashes: nothing changes on the way.
    again = exported(client, new_id)
    assert again["character"] == document["character"]
    assert sorted(item["sha256"] for item in again["assets"]) == sorted(item["sha256"] for item in document["assets"])


def test_the_voice_and_wardrobe_fields_survive_the_round_trip(client: TestClient) -> None:
    source = create_character(client)
    new_id = import_document(client, exported(client, source)).json()["character"]["id"]
    card = client.get(f"{CHARACTERS}/{new_id}").json()["card"]
    communication, visual = card["communication"], card["visual"]
    assert card["identity"]["grammatical_gender"] == "femminile"
    assert (communication["address_form"], communication["narration"], communication["emoji"]) == (
        "lei",
        "leggera",
        "mai",
    )
    assert communication["examples"] == CARD["communication"]["examples"]
    assert communication["avoid"] == CARD["communication"]["avoid"]
    assert visual["outfits"] == CARD["visual"]["outfits"]
    assert visual["usual_outfit_en"] == CARD["visual"]["usual_outfit_en"]
    assert card["traits"][1]["locked"] is True


def test_an_export_without_assets_lists_hashes_and_imports_without_images(client: TestClient) -> None:
    source = create_character(client)
    data = picture("PNG", (1, 2, 3))
    upload(client, source, data, "image/png")
    document = exported(client, source, include_assets=False)
    (entry,) = document["assets"]
    assert "data_base64" not in entry
    assert entry["sha256"] == assets_of(client, source)[0]["sha256"]
    body = import_document(client, document).json()
    assert (body["assets_imported"], body["assets_without_data"]) == (0, 1)
    assert assets_of(client, body["character"]["id"]) == []


def test_an_export_is_a_named_download_in_the_documented_shape(client: TestClient) -> None:
    source = create_character(client)
    response = client.get(f"{CHARACTERS}/{source}/export")
    assert response.headers["content-disposition"] == 'attachment; filename="nerea.lyrveil-character.json"'
    document = response.json()
    assert list(document) == ["format", "format_version", "exported_at", "generator", "character", "assets"]
    assert (document["format"], document["format_version"], document["assets"]) == ("lyrveil.character", 1, [])
    assert client.get(f"{CHARACTERS}/unknown/export").status_code == 404


def test_every_export_validates_against_the_published_schema(client: TestClient) -> None:
    schema = client.get(f"{CHARACTERS}/format-schema")
    assert schema.status_code == 200
    published = schema.json()
    assert published == json_schema()
    assert published["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert published["additionalProperties"] is False
    assert set(published["required"]) == {"format", "format_version", "exported_at", "generator", "character"}
    source = create_character(client)
    upload(client, source, picture("PNG", (9, 9, 9)), "image/png")
    minimal = client.post(CHARACTERS, json={"identity": {"first_name": "Alba", "age": 30}}).json()["id"]
    for character_id in (source, minimal):
        for include in (True, False):
            ExchangeFile.model_validate(exported(client, character_id, include_assets=include))


def refused(client: TestClient, document: dict[str, Any] | bytes, detail: str) -> None:
    before = character_count(client)
    response = import_document(client, document)
    assert (response.status_code, response.json()["code"]) == (422, "IMPORT_INVALID"), response.text
    assert response.json()["detail"] == detail
    assert character_count(client) == before


def test_a_newer_format_is_refused(client: TestClient) -> None:
    document = exported(client, create_character(client))
    document["format_version"] = 2
    document["something_new"] = True
    refused(
        client,
        document,
        "Il file usa una versione del formato più recente di quella supportata. Aggiorna Lyrveil.",
    )


def test_another_format_or_text_that_is_not_json_is_refused(client: TestClient) -> None:
    document = exported(client, create_character(client))
    refused(client, {**document, "format": "chara_card_v2"}, "Il file non è un personaggio di Lyrveil.")
    refused(client, b"not json", "Il file non è un JSON valido.")
    refused(client, b"[1, 2]", "Il file non è un personaggio di Lyrveil.")
    refused(client, {**document, "format_version": "1"}, "La versione del formato non è valida.")


def test_unknown_fields_are_refused_in_the_file_and_in_the_card(client: TestClient) -> None:
    document = exported(client, create_character(client))
    refused(client, {**document, "extra": 1}, "Il campo «extra» non è previsto.")
    typo = copy.deepcopy(document)
    typo["character"]["communication"]["emojii"] = "mai"
    refused(client, typo, "Il campo «communication.emojii» non è previsto.")
    asset_typo = copy.deepcopy(document)
    asset_typo["assets"] = [
        {"kind": "avatar", "is_primary": False, "media_type": "image/png", "sha256": "0" * 64, "path": "x"}
    ]
    refused(client, asset_typo, "Il campo «assets.0.path» non è previsto.")


def test_a_minor_is_refused_at_import(client: TestClient) -> None:
    document = exported(client, create_character(client))
    document["character"]["identity"]["age"] = 17
    refused(client, document, "L'età deve essere almeno 18.")


def test_a_card_the_rules_refuse_is_refused_at_import(client: TestClient) -> None:
    document = exported(client, create_character(client))
    unknown_tag = copy.deepcopy(document)
    unknown_tag["character"]["visual"]["identity_en"]["hair_color"] = "plaid hair"
    refused(client, unknown_tag, "Il valore di «visual.identity_en.hair_color» non è nel vocabolario dei tag.")
    reserved = copy.deepcopy(document)
    reserved["character"]["visual"]["outfits"][0]["key"] = "usual"
    before = character_count(client)
    assert import_document(client, reserved).status_code == 422
    assert character_count(client) == before


def test_an_image_with_a_wrong_hash_or_bytes_is_refused(client: TestClient) -> None:
    source = create_character(client)
    upload(client, source, picture("PNG", (200, 40, 40)), "image/png")
    document = exported(client, source)
    wrong_hash = copy.deepcopy(document)
    wrong_hash["assets"][0]["sha256"] = "f" * 64
    refused(client, wrong_hash, "L'impronta di un'immagine non corrisponde ai suoi dati.")
    bad_base64 = copy.deepcopy(document)
    bad_base64["assets"][0]["data_base64"] = "***"
    refused(client, bad_base64, "Un'immagine del file non è codificata correttamente.")
    # The hash is right, but the bytes are no image.
    not_an_image = copy.deepcopy(document)
    junk = b"plain text, not a picture"
    not_an_image["assets"][0]["data_base64"] = base64.b64encode(junk).decode()
    not_an_image["assets"][0]["sha256"] = hashlib.sha256(junk).hexdigest()
    before = character_count(client)
    response = import_document(client, not_an_image)
    assert (response.status_code, response.json()["code"]) == (422, "IMPORT_INVALID")
    assert character_count(client) == before


def test_two_primary_avatars_in_a_file_are_refused(client: TestClient) -> None:
    source = create_character(client)
    upload(client, source, picture("PNG", (1, 1, 1)), "image/png")
    upload(client, source, picture("PNG", (2, 2, 2)), "image/png")
    document = exported(client, source)
    for entry in document["assets"]:
        entry["is_primary"] = True
    refused(client, document, "Il file indica un avatar principale non valido.")


def test_an_oversized_file_is_refused_before_it_is_read(engine: Engine, media_root: Path) -> None:
    upgrade_to_head(engine)
    service = CharacterExchangeService(engine, MediaStore(media_root))
    with pytest.raises(PayloadTooLargeError):
        service.import_file(b" " * (MAX_IMPORT_BYTES + 1))
