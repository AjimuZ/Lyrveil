"""T-LOR-05 and T-LOR-08 through the API on a real database (P12.4): a card's `character_book` becomes the character's
own book when the import is saved, the card export carries it back, and a `lorebook_v3` file imports as a world and
exports with every field."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from lyrveil.jobs.scheduler import read
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence import lore as repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

API = "/api/v1"
CHARACTERS = f"{API}/characters"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tavern_cards"


@pytest.fixture
def client(settings: Settings, engine: Engine, tmp_path: Path) -> TestClient:
    upgrade_to_head(engine)
    app = create_app(settings, engine, media_store=MediaStore(tmp_path / "data" / "media"))
    return TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)


def entry(keys: list[str], content: str, order: int, **fields: Any) -> dict[str, Any]:
    return {"keys": keys, "content": content, "extensions": {}, "enabled": True, "insertion_order": order, **fields}


def card_with_book() -> dict[str, Any]:
    """The V3 fixture, whose one entry keeps a `@@depth` line, with an entry for each rule of the import."""
    document: dict[str, Any] = json.loads((FIXTURES / "v3.json").read_text(encoding="utf-8"))
    document["data"]["character_book"]["entries"] += [
        entry(["kiln"], "@@activate\nThe kiln is always lit.", 60),
        entry(["glass"], "@@dont_activate\nAn old note about glass.", 70),
        entry(
            ["pipe"],
            "@@additional_keys blowpipe, cane\n@@exclude_keys tea\nThe pipe is iron.",
            80,
            name="Pipe",
            comment="Tools",
        ),
        entry(["oven"], "The oven is new.", 90, use_regex=True),
        entry([], "A fact with no key.", 100),
        entry(["sand"], "Sand comes from the river.", 110, position="after_char", custom={"a": 1}),
    ]
    return document


def upload_card(client: TestClient, document: dict[str, Any]) -> dict[str, Any]:
    content = json.dumps(document).encode("utf-8")
    response = client.post(f"{CHARACTERS}/imports", files={"file": ("card.json", content, "application/json")})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def save(client: TestClient, imported: dict[str, Any]) -> str:
    draft = imported["direct_draft"]["draft"]
    body = {
        "identity": {**draft["identity"], "age": 45},
        "traits": draft["traits"],
        "communication": draft["communication"],
        "import_id": imported["id"],
    }
    response = client.post(CHARACTERS, json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def upload_lorebook(client: TestClient, document: object) -> Any:
    content = json.dumps(document).encode("utf-8")
    return client.post(f"{API}/lore/worlds/import", files={"file": ("book.json", content, "application/json")})


def test_a_saved_card_import_turns_its_character_book_into_the_own_book(client: TestClient, engine: Engine) -> None:
    """T-LOR-05: the field mapping, the four decorators applied or kept, the regex and keyless entries disabled and
    reported, and the unknown fields kept."""
    imported = upload_card(client, card_with_book())
    lore = imported["report"]["lore"]
    assert (lore["entries"], lore["disabled"]) == (7, 3)
    assert [(item["index"], item["reason"]) for item in lore["items"]] == [(4, "use_regex"), (5, "no_usable_key")]
    assert all(item["detail"] for item in lore["items"])

    character_id = save(client, imported)
    book = client.get(f"{CHARACTERS}/{character_id}/lore").json()
    assert (book["kind"], book["name"], book["entry_count"]) == ("character", "Workshop", 7)
    entries = client.get(f"{API}/lore/books/{book['id']}/entries").json()["items"]
    by_content = {item["content"]: item for item in entries}

    furnace = by_content["The furnace never goes cold between October and May."]
    assert (furnace["keys"], furnace["insertion_order"], furnace["enabled"]) == (["furnace"], 50, True)
    assert by_content["The kiln is always lit."]["constant"] is True
    assert by_content["An old note about glass."]["enabled"] is False
    pipe = by_content["The pipe is iron."]
    assert (pipe["title"], pipe["note"]) == ("Pipe", "Tools")
    assert (pipe["keys"], pipe["exclude_keys"]) == (["pipe", "blowpipe", "cane"], ["tea"])
    assert by_content["The oven is new."]["enabled"] is False
    assert by_content["A fact with no key."]["enabled"] is False

    stored = read(engine, lambda connection: repository.list_entries(connection, book["id"]))
    sources = {item.content: item.source for item in stored}
    assert sources["The furnace never goes cold between October and May."]["decorators"] == ["@@depth 4"]
    assert sources["Sand comes from the river."]["fields"] == {
        "extensions": {},
        "position": "after_char",
        "custom": {"a": 1},
    }
    assert sources["The oven is new."]["fields"]["use_regex"] is True


def test_the_card_export_carries_the_own_book_as_character_book(client: TestClient) -> None:
    character_id = save(client, upload_card(client, card_with_book()))
    for card_format in ("v3json", "v2json"):
        response = client.get(f"{CHARACTERS}/{character_id}/card", params={"format": card_format})
        assert response.status_code == 200, response.text
        book = response.json()["data"]["character_book"]
        assert book["name"] == "Workshop"
        assert [item["insertion_order"] for item in book["entries"]] == [50, 60, 70, 80, 90, 100, 110]
        first = book["entries"][0]
        assert first["content"] == "@@depth 4\nThe furnace never goes cold between October and May."
        assert (first["keys"], first["use_regex"]) == (["furnace"], False)
        regex = book["entries"][4]
        # Lyrveil disabled it on import; the export gives it back as the file had it.
        assert (regex["keys"], regex["use_regex"], regex["enabled"]) == (["oven"], True, True)
        assert book["entries"][3]["content"] == "@@exclude_keys tea\nThe pipe is iron."
        assert book["entries"][6]["custom"] == {"a": 1}

    # A character with an own book but no entry exports no `character_book`.
    other = client.post(
        CHARACTERS,
        json={
            "identity": {"first_name": "Nerea", "age": 27},
            "traits": [{"category": "main_trait", "description": "Ironica."}],
            "communication": {"examples": []},
            "visual": {},
        },
    )
    assert other.status_code == 201, other.text
    assert client.get(f"{CHARACTERS}/{other.json()['id']}/lore").status_code == 200
    assert "character_book" not in client.get(f"{CHARACTERS}/{other.json()['id']}/card").json()["data"]


LOREBOOK: dict[str, Any] = {
    "spec": "lorebook_v3",
    "data": {
        "name": "Aldara",
        "description": "Un regno di mare.",
        "scan_depth": 6,
        "token_budget": 2048,
        "recursive_scanning": True,
        "extensions": {"tool": {"x": 1}},
        "custom_book_field": "kept",
        "entries": [
            entry(
                ["faro", "lanterna"],
                "@@depth 4\n@@position after_desc\nIl faro di Aldara è spento da un secolo.",
                10,
                case_sensitive=False,
                use_regex=False,
                constant=False,
                name="Il faro",
                priority=5,
                id=7,
                comment="Nota",
                selective=True,
                secondary_keys=["notte"],
                position="after_char",
                unknown_field=[1, 2],
            ),
            entry(
                ["porto"], "Il porto chiude al tramonto.", 20, use_regex=False, selective=False, secondary_keys=["nave"]
            ),
            entry(["/re(gno|ame)/i"], "Il regno ha tre isole.", 30, use_regex=True, id=9),
            entry(["lui"], "Una voce senza chiavi utili.", 40, use_regex=False),
        ],
    },
}


def test_a_lorebook_round_trip_keeps_every_field(client: TestClient) -> None:
    """T-LOR-08: import as a world, then export: every field of the file comes back, and so do the decorators that
    were not applied, as lines at the start of the content."""
    response = upload_lorebook(client, LOREBOOK)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["book"]["kind"], body["book"]["name"], body["book"]["token_budget"]) == ("world", "Aldara", None)
    assert body["report"]["entries"] == 4
    assert [(item["index"], item["reason"]) for item in body["report"]["items"]] == [
        (2, "use_regex"),
        (3, "keys_left_out"),
        (3, "no_usable_key"),
    ]

    exported = client.get(f"{API}/lore/books/{body['book']['id']}/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-disposition"] == 'attachment; filename="aldara.lorebook.json"'
    document = exported.json()
    assert document["spec"] == "lorebook_v3"
    original = LOREBOOK["data"]
    for name, value in original.items():
        if name != "entries":
            assert document["data"][name] == value, name
    for source, written in zip(original["entries"], document["data"]["entries"], strict=True):
        for name, value in source.items():
            assert written[name] == value, name

    # The exported file imports again into an equal world.
    again = upload_lorebook(client, document)
    assert again.status_code == 201, again.text
    second = client.get(f"{API}/lore/books/{again.json()['book']['id']}/export").json()
    assert second == document


def test_a_file_that_is_not_a_lorebook_or_describes_a_minor_stores_nothing(client: TestClient, engine: Engine) -> None:
    wrong = upload_lorebook(client, {"spec": "chara_card_v3", "data": {"name": "Aldara"}})
    assert (wrong.status_code, wrong.json()["code"]) == (422, "IMPORT_INVALID")
    document = json.loads(json.dumps(LOREBOOK))
    document["data"]["entries"][1]["content"] = "Una bambina di sette anni abita al porto."
    blocked = upload_lorebook(client, document)
    assert (blocked.status_code, blocked.json()["code"]) == (422, "CONTENT_POLICY_BLOCKED")
    assert "data.entries[1].content" in blocked.json()["detail"]
    assert client.get(f"{API}/lore/worlds").json()["items"] == []
    assert client.get(f"{API}/lore/books/missing/export").status_code == 404
