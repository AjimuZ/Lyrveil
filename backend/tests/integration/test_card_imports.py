"""T-CRD-02 (P10.2a): a card import stores the parsed card unchanged with the file hash, the source routes return it,
any refusal stores no row and no file, an unsaved import expires with its staged avatar, and an import makes no
outbound request, through the API on a real database."""

import base64
import hashlib
import io
import json
import socket
import struct
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.errors import NotFoundError
from lyrveil.jobs.scheduler import read, write
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.card_import import CardImportService
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"
IMPORTS = f"{CHARACTERS}/imports"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tavern_cards"


def card(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


def card_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def card_png(document: object) -> bytes:
    """A real 8 x 8 PNG with the card in a `chara` chunk and a second text chunk, which the avatar must not keep."""
    info = PngImagePlugin.PngInfo()
    info.add_text("chara", base64.b64encode(card_bytes(document)).decode("ascii"))
    info.add_text("Comment", "a chunk that only the upload holds")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 80, 40)).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def chunk(kind: bytes, body: bytes) -> bytes:
    return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(body, zlib.crc32(kind)).to_bytes(4, "big")


def undecodable_png(document: object) -> bytes:
    """A PNG whose chunks are all well formed, so the parser reads the card, but whose pixels are not an image."""
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
    text_chunk = chunk(b"tEXt", b"chara\0" + base64.b64encode(card_bytes(document)))
    return b"\x89PNG\r\n\x1a\n" + header + text_chunk + chunk(b"IDAT", b"not zlib") + chunk(b"IEND", b"")


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "media"


@pytest.fixture
def client(settings: Settings, engine: Engine, media_root: Path) -> TestClient:
    upgrade_to_head(engine)
    app = create_app(settings, engine, media_store=MediaStore(media_root))
    return TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)


def upload(client: TestClient, content: bytes, media_type: str = "application/json") -> Any:
    return client.post(IMPORTS, files={"file": ("card", content, media_type)})


def count(engine: Engine, table: str) -> int:
    return read(engine, lambda connection: int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()))


def staged_files(media_root: Path) -> list[Path]:
    staging = media_root / ".staging" / "imports"
    return sorted(path for path in staging.rglob("*") if path.is_file()) if staging.is_dir() else []


def test_an_import_stores_the_parsed_card_unchanged_with_the_file_hash(
    client: TestClient, engine: Engine, media_root: Path
) -> None:
    content = card_bytes(card("v2.json"))
    response = upload(client, content)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert (body["format"], body["container"]) == ("chara_card_v2", "json")
    assert body["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert body["conversion_job_id"] is None
    assert body["expires_at"] > "2026"
    assert body["preview"] == {
        "name": "Livia Sarno",
        "nickname": "",
        "creator": "Lyrveil tests",
        "version": "1.2",
        "tags": ["slice of life", "sea"],
        "creator_notes": "Synthetic card written for the Lyrveil parser tests.",
        "system_prompt": "Write as {{char}} in third person.",
        "post_history_instructions": "Keep replies under three paragraphs.",
        "greeting_count": 3,
        "has_avatar": False,
        "has_lorebook": True,
    }
    assert client.get(f"{IMPORTS}/{body['id']}").json() == body
    source = client.get(f"{IMPORTS}/{body['id']}/source")
    assert source.headers["content-type"] == "application/json"
    assert source.json() == card("v2.json")
    stored = read(engine, lambda c: c.execute(text("SELECT source_json, source_sha256 FROM card_imports")).one())
    assert json.loads(stored[0]) == card("v2.json")
    assert stored[1] == body["source_sha256"]
    # No import writes a character, an asset or a version.
    assert [count(engine, table) for table in ("characters", "character_assets", "character_versions")] == [0, 0, 0]
    assert client.get(CHARACTERS).json() == {"items": []}
    assert staged_files(media_root) == []


def test_the_import_answers_the_direct_draft_and_its_report(client: TestClient) -> None:
    """P10.2b: the import stores the direct mapping, and the card-only fields stay out of the draft."""
    document = card("v2.json")
    document["data"]["personality"] = "<BOT> ride {{random: spesso, mai}}."
    body = upload(client, card_bytes(document)).json()

    direct = body["direct_draft"]
    assert direct["draft"]["identity"]["first_name"] == "Livia Sarno"
    assert direct["draft"]["identity"]["age"] == 31
    assert direct["draft"]["traits"] == [{"category": "main_trait", "description": "{{char}} ride spesso."}]
    assert direct["card_fields"] == [
        "identity.first_name",
        "identity.age",
        "identity.biography",
        "traits",
        "communication.examples",
    ]
    assert [scenario["title"] for scenario in direct["scenarios"]] == [
        "Livia Sarno",
        "Livia Sarno (saluto 2)",
        "Livia Sarno (saluto 3)",
    ]
    assert body["report"]["age"] == {"status": "stated", "value": 31, "source": "data.description"}
    assert [(item["macro"], item["action"]) for item in body["report"]["macros"]] == [
        ("<BOT>", "placeholder"),
        ("{{random: spesso, mai}}", "first_option"),
    ]
    assert body["report"]["left_out"] == []
    draft_text = json.dumps(direct)
    for card_only in ("Write as", "Keep replies under three paragraphs.", "Synthetic card written"):
        assert card_only not in draft_text
    assert client.get(f"{IMPORTS}/{body['id']}").json() == body


@pytest.mark.parametrize(("name", "format_name"), [("v1.json", "chara_card_v1"), ("v3.json", "chara_card_v3")])
def test_v1_and_v3_cards_keep_their_own_format(client: TestClient, name: str, format_name: str) -> None:
    body = upload(client, card_bytes(card(name))).json()
    assert body["format"] == format_name
    assert client.get(f"{IMPORTS}/{body['id']}/source").json() == card(name)


def test_a_png_card_stages_a_re_encoded_avatar_without_any_chunk(
    client: TestClient, engine: Engine, media_root: Path
) -> None:
    content = card_png(card("v2.json"))
    body = upload(client, content, "image/png").json()
    assert (body["container"], body["preview"]["has_avatar"]) == ("png", True)
    assert body["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert client.get(f"{IMPORTS}/{body['id']}/source").json() == card("v2.json")
    path = read(engine, lambda c: c.execute(text("SELECT avatar_staging_path FROM card_imports")).scalar_one())
    assert path == f".staging/imports/{body['id']}/avatar.png"
    assert staged_files(media_root) == [media_root / path]
    staged = (media_root / path).read_bytes()
    assert staged.startswith(b"\x89PNG")
    assert b"tEXt" not in staged
    assert b"chara" not in staged
    with Image.open(io.BytesIO(staged)) as image:
        assert image.size == (8, 8)
        assert image.info == {}


def test_discarding_an_import_removes_its_row_and_its_staged_avatar(client: TestClient, media_root: Path) -> None:
    body = upload(client, card_png(card("v3.json")), "image/png").json()
    assert len(staged_files(media_root)) == 1
    assert client.delete(f"{IMPORTS}/{body['id']}").status_code == 204
    assert staged_files(media_root) == []
    assert not (media_root / ".staging" / "imports" / body["id"]).exists()
    for response in (client.get(f"{IMPORTS}/{body['id']}"), client.get(f"{IMPORTS}/{body['id']}/source")):
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")
    assert client.delete(f"{IMPORTS}/{body['id']}").status_code == 404


def test_a_saved_import_cannot_be_discarded(client: TestClient, engine: Engine) -> None:
    body = upload(client, card_bytes(card("v2.json"))).json()
    write(engine, lambda c: c.execute(text("UPDATE card_imports SET status = 'saved'")))
    response = client.delete(f"{IMPORTS}/{body['id']}")
    assert (response.status_code, response.json()["code"]) == (409, "CONFLICT")
    assert client.get(f"{IMPORTS}/{body['id']}").status_code == 200


def blocked_card() -> dict[str, Any]:
    document = card("v2.json")
    document["data"]["character_book"]["entries"][3]["content"] = "The harbour master's helper is a child."
    return document


def bad_chunk_png() -> bytes:
    good = card_png(card("v2.json"))
    index = good.index(b"IDAT") + 6
    return good[:index] + bytes([good[index] ^ 0xFF]) + good[index + 1 :]


@pytest.mark.parametrize(
    ("content", "media_type", "status", "code"),
    [
        pytest.param(b'{"spec": ', "application/json", 422, "IMPORT_INVALID", id="broken JSON"),
        pytest.param(b"PK\x03\x04charx", "application/zip", 415, "UNSUPPORTED_MEDIA", id="CHARX"),
        pytest.param(b"hello", "text/plain", 415, "UNSUPPORTED_MEDIA", id="not a card file"),
        pytest.param(card_bytes(blocked_card()), "application/json", 422, "CONTENT_POLICY_BLOCKED", id="minor in JSON"),
        pytest.param(card_png(blocked_card()), "image/png", 422, "CONTENT_POLICY_BLOCKED", id="minor in PNG"),
        pytest.param(bad_chunk_png(), "image/png", 422, "IMPORT_INVALID", id="bad chunk CRC"),
        pytest.param(undecodable_png(card("v2.json")), "image/png", 415, "UNSUPPORTED_MEDIA", id="avatar not an image"),
    ],
)
def test_a_refusal_stores_no_row_and_no_file(
    client: TestClient, engine: Engine, media_root: Path, content: bytes, media_type: str, status: int, code: str
) -> None:
    response = upload(client, content, media_type)
    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    assert count(engine, "card_imports") == 0
    assert staged_files(media_root) == []


def test_a_content_refusal_names_the_source_path(client: TestClient) -> None:
    response = upload(client, card_bytes(blocked_card()))
    assert response.json()["code"] == "CONTENT_POLICY_BLOCKED"
    assert "«data.character_book.entries[3].content»" in response.json()["detail"]
    assert "child" not in response.json()["detail"]


def test_an_import_makes_no_outbound_request_and_keeps_remote_uris(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = card("v3.json")
    remote = "https://cards.example.invalid/livia.png"
    document["data"]["assets"] = [{"type": "icon", "uri": remote, "name": "main", "ext": "png"}]
    document["data"]["extensions"] = {"image": remote}
    attempts: list[object] = []

    def refuse(*arguments: object, **keywords: object) -> None:
        attempts.append(arguments)
        raise AssertionError("an import must not open a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for content, media_type in ((card_bytes(document), "application/json"), (card_png(document), "image/png")):
        response = upload(client, content, media_type)
        assert response.status_code == 201, response.text
        assert client.get(f"{IMPORTS}/{response.json()['id']}/source").json() == document
    assert attempts == []
    assert count(engine, "card_imports") == 2


def test_an_unsaved_import_expires_after_seven_days_with_its_staged_avatar(engine: Engine, media_root: Path) -> None:
    upgrade_to_head(engine)
    made = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    media = MediaStore(media_root)
    imported = CardImportService(engine, media, clock=lambda: made).import_file(card_png(card("v2.json")))
    kept = CardImportService(engine, media, clock=lambda: made).import_file(card_bytes(card("v1.json")))
    mark_saved = text("UPDATE card_imports SET status = 'saved' WHERE id = :id")
    write(engine, lambda c: c.execute(mark_saved, {"id": kept.stored.id}))
    assert imported.stored.expires_at == "2026-12-04T10:00:00.000Z"
    assert len(staged_files(media_root)) == 1

    almost = CardImportService(engine, media, clock=lambda: made + timedelta(days=6, hours=23))
    assert almost.get(imported.stored.id).stored.id == imported.stored.id
    assert almost.expire() == 0
    assert len(staged_files(media_root)) == 1

    later = CardImportService(engine, media, clock=lambda: made + timedelta(days=7))
    with pytest.raises(NotFoundError):
        later.get(imported.stored.id)
    # The row still exists until the clean-up runs, and the saved import is never expired.
    assert count(engine, "card_imports") == 2
    assert later.expire() == 1
    assert count(engine, "card_imports") == 1
    assert staged_files(media_root) == []
    assert not (media_root / ".staging" / "imports" / imported.stored.id).exists()
    assert later.get(kept.stored.id).stored.status == "saved"


def insert_source(connection: Connection, character_id: str, source: dict[str, Any]) -> None:
    connection.execute(
        text(
            "INSERT INTO character_sources (id, character_id, format, container, source_json, source_sha256, "
            "imported_at) VALUES ('s1', :character, 'chara_card_v2', 'json', :source, 'h', '2026-11-27T10:00:00.000Z')"
        ),
        {"character": character_id, "source": json.dumps(source)},
    )


def test_the_character_source_route_returns_the_stored_card_and_a_purge_deletes_it(
    client: TestClient, engine: Engine
) -> None:
    character_id = client.post(CHARACTERS, json={"identity": {"first_name": "Alba", "age": 30}}).json()["id"]
    missing = client.get(f"{CHARACTERS}/{character_id}/source")
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
    unknown = client.get(f"{CHARACTERS}/nope/source")
    assert unknown.status_code == 404
    write(engine, lambda c: insert_source(c, character_id, card("v2.json")))
    found = client.get(f"{CHARACTERS}/{character_id}/source")
    assert found.status_code == 200
    assert found.json() == card("v2.json")
    assert client.post(f"{CHARACTERS}/{character_id}/purge", json={"confirm_name": "Alba"}).status_code == 204
    assert count(engine, "character_sources") == 0
