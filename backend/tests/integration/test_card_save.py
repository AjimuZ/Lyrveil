"""T-CRD-04 at the API (P10.4): saving an import through `POST /characters` with `import_id` applies every card rule,
writes the character at version 1 by the actor `import` with its source, avatar and the scenarios that were asked for in
one transaction, and an import cannot be saved twice, through the API on a real database."""

import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import Engine, text

from lyrveil.jobs.scheduler import read
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"
IMPORTS = f"{CHARACTERS}/imports"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tavern_cards"


def card(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


def card_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def png_bytes(info: PngImagePlugin.PngInfo | None = None) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 80, 40)).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def card_png(document: object) -> bytes:
    """A real PNG with the card in a `chara` chunk and a second text chunk, which the avatar must not keep."""
    info = PngImagePlugin.PngInfo()
    info.add_text("chara", base64.b64encode(card_bytes(document)).decode("ascii"))
    info.add_text("Comment", "a chunk that only the upload holds")
    return png_bytes(info)


def with_icon(document: dict[str, Any], data: bytes) -> dict[str, Any]:
    uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    document["data"]["assets"] = [{"type": "icon", "uri": uri, "name": "main", "ext": "png"}]
    return document


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "media"


@pytest.fixture
def client(settings: Settings, engine: Engine, media_root: Path) -> TestClient:
    upgrade_to_head(engine)
    app = create_app(settings, engine, media_store=MediaStore(media_root))
    return TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)


def upload(client: TestClient, content: bytes, media_type: str = "application/json") -> dict[str, Any]:
    response = client.post(IMPORTS, files={"file": ("card", content, media_type)})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def save_body(imported: dict[str, Any], **identity: Any) -> dict[str, Any]:
    """The reviewed draft of an import as the Creator sends it, with the user's changes to the identity."""
    draft = imported["direct_draft"]["draft"]
    return {
        "identity": {**draft["identity"], **identity},
        "traits": draft["traits"],
        "communication": draft["communication"],
        "import_id": imported["id"],
    }


def rows(engine: Engine, sql: str) -> list[tuple[Any, ...]]:
    return read(engine, lambda connection: [tuple(row) for row in connection.execute(text(sql))])


def staged_files(media_root: Path) -> list[Path]:
    staging = media_root / ".staging" / "imports"
    return sorted(path for path in staging.rglob("*") if path.is_file()) if staging.is_dir() else []


def test_saving_an_import_writes_the_character_its_source_and_the_scenarios(client: TestClient, engine: Engine) -> None:
    imported = upload(client, card_bytes(card("v2.json")))
    response = client.post(CHARACTERS, json=save_body(imported))
    assert response.status_code == 201, response.text
    character_id = response.json()["id"]
    assert (response.json()["first_name"], response.json()["age"]) == ("Livia Sarno", 31)

    versions = client.get(f"{CHARACTERS}/{character_id}/versions").json()["items"]
    assert [(item["version_no"], item["actor"]) for item in versions] == [(1, "import")]
    assert client.get(f"{CHARACTERS}/{character_id}/source").json() == card("v2.json")
    assert rows(engine, "SELECT status, character_id FROM card_imports") == [("saved", character_id)]
    assert rows(engine, "SELECT character_id, source_sha256 FROM character_sources") == [
        (character_id, imported["source_sha256"])
    ]
    titles = rows(engine, "SELECT title FROM scenarios ORDER BY title")
    assert titles == [("Livia Sarno",), ("Livia Sarno (saluto 2)",), ("Livia Sarno (saluto 3)",)]
    assert rows(engine, "SELECT COUNT(*) FROM scenarios WHERE character_id IS NULL") == [(0,)]
    # A card without an image gives a character without an avatar.
    assert rows(engine, "SELECT COUNT(*) FROM character_assets") == [(0,)]


def test_greetings_become_scenarios_only_when_asked_and_group_only_ones_never(
    client: TestClient, engine: Engine
) -> None:
    imported = upload(client, card_bytes(card("v3.json")))
    body = {**save_body(imported, age=30), "create_scenarios": False}
    assert client.post(CHARACTERS, json=body).status_code == 201
    assert rows(engine, "SELECT COUNT(*) FROM scenarios") == [(0,)]

    asked = upload(client, card_bytes(card("v3.json")))
    assert client.post(CHARACTERS, json={**save_body(asked, age=30), "create_scenarios": True}).status_code == 201
    stored = rows(engine, "SELECT title, opening_message FROM scenarios ORDER BY title")
    assert len(stored) == 2
    assert not any("stand behind the line" in message for _, message in stored)


def test_a_missing_or_under_age_age_is_refused_and_nothing_is_written(client: TestClient, engine: Engine) -> None:
    imported = upload(client, card_bytes(card("v3.json")))
    for age in (None, 17):
        response = client.post(CHARACTERS, json=save_body(imported, age=age))
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR"), response.text
    absent = save_body(imported)
    del absent["identity"]["age"]
    assert client.post(CHARACTERS, json=absent).status_code == 422
    assert rows(engine, "SELECT COUNT(*) FROM characters") == [(0,)]
    assert rows(engine, "SELECT COUNT(*) FROM character_sources") == [(0,)]
    assert rows(engine, "SELECT COUNT(*) FROM scenarios") == [(0,)]
    assert rows(engine, "SELECT status FROM card_imports") == [("ready",)]
    assert client.post(CHARACTERS, json=save_body(imported, age=30)).status_code == 201


def test_an_import_cannot_be_saved_twice(client: TestClient, engine: Engine) -> None:
    imported = upload(client, card_bytes(card("v2.json")))
    assert client.post(CHARACTERS, json=save_body(imported)).status_code == 201
    again = client.post(CHARACTERS, json=save_body(imported))
    assert (again.status_code, again.json()["code"]) == (409, "CONFLICT")
    assert rows(engine, "SELECT COUNT(*) FROM characters") == [(1,)]
    assert rows(engine, "SELECT COUNT(*) FROM scenarios") == [(3,)]
    unknown = client.post(CHARACTERS, json={**save_body(imported), "import_id": "00000000-0000-4000-8000-000000000000"})
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")


def test_the_avatar_of_a_png_card_is_the_primary_one_without_any_chunk(
    client: TestClient, engine: Engine, media_root: Path
) -> None:
    imported = upload(client, card_png(card("v2.json")), "image/png")
    assert len(staged_files(media_root)) == 1
    response = client.post(CHARACTERS, json=save_body(imported))
    assert response.status_code == 201, response.text
    character_id = response.json()["id"]
    assert rows(engine, "SELECT kind, source, is_primary FROM character_assets") == [("avatar", "upload", 1)]
    stored_path = rows(engine, "SELECT file_path FROM character_assets")[0][0]
    stored = (media_root / stored_path).read_bytes()
    assert stored.startswith(b"\x89PNG")
    assert b"tEXt" not in stored
    assert rows(engine, "SELECT primary_avatar_asset_id IS NOT NULL FROM character_visual_profiles") == [(1,)]
    assert stored_path.startswith(f"assets/{character_id}/")
    assert staged_files(media_root) == []


def test_a_json_card_with_a_main_icon_data_uri_gets_that_avatar(client: TestClient, engine: Engine) -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "metadata that must not survive")
    imported = upload(client, card_bytes(with_icon(card("v2.json"), png_bytes(info))))
    assert client.post(CHARACTERS, json=save_body(imported)).status_code == 201
    assert rows(engine, "SELECT kind, is_primary, media_type FROM character_assets") == [("avatar", 1, "image/png")]


def test_an_icon_that_is_not_an_image_gives_no_avatar_and_the_save_still_works(
    client: TestClient, engine: Engine
) -> None:
    imported = upload(client, card_bytes(with_icon(card("v2.json"), b"not an image")))
    assert client.post(CHARACTERS, json=save_body(imported)).status_code == 201
    assert rows(engine, "SELECT COUNT(*) FROM character_assets") == [(0,)]


def test_the_save_fields_are_checked(client: TestClient) -> None:
    imported = upload(client, card_bytes(card("v2.json")))
    for extra in ({"import_id": ""}, {"import_id": 3}, {"create_scenarios": "no"}):
        response = client.post(CHARACTERS, json={**save_body(imported), **extra})
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR"), extra
    body = save_body(imported)
    del body["import_id"]
    response = client.post(CHARACTERS, json={**body, "create_scenarios": True})
    assert response.status_code == 422
