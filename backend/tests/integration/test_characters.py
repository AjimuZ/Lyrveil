"""Checks the character routes (create with the whole card, read, list) through the API on a real database."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from lyrveil.jobs.scheduler import write
from lyrveil.main import create_app
from lyrveil.persistence.database import create_database_engine
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.characters import CharacterService
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


FULL_CARD: dict[str, Any] = {
    "identity": {
        "first_name": "Nerea",
        "last_name": "Falchi",
        "age": 27,
        "grammatical_gender": "femminile",
        "narrative_identity": "Cartografa di una città portuale.",
        "biography": "È cresciuta sopra una bottega di bussole.",
        "occupation": "Cartografa indipendente",
        "interests": ["mappe antiche", "gatti di porto"],
        "background": "Ha lasciato la gilda dei cartografi.",
        "setting": "Velmara, città di mare",
        "primary_language": "it",
    },
    "traits": [
        {"category": "main_trait", "description": "Ironica, curiosa.", "locked": False},
        {"category": "fear", "description": "Perdersi in un posto noto.", "locked": True},
    ],
    "communication": {
        "formality": "informale",
        "message_length": "brevi",
        "question_frequency": "a_volte",
        "punctuation": "Pochi punti esclamativi.",
        "colloquial_style": "Scrive di getto.",
        "expressiveness": "media",
        "initiative_frequency": "a_volte",
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
        "face": "Viso spigoloso.",
        "hair": "Capelli rossi corti.",
        "subject_tag": "1girl",
        "identity_en": {
            "hair_color": "red hair",
            "hair_length": "short hair",
            "eye_color": "green eyes",
            "skin": None,
            "hairstyle": ["messy hair"],
            "face": ["freckles"],
            "body": ["slim"],
            "marks": ["ink stains"],
            "accessories": ["necklace"],
            "style": ["soft colors"],
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


def create_full(client: TestClient) -> str:
    response = client.post(URL, json=FULL_CARD)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_a_minimal_card_reads_back_with_the_defaults(client: TestClient) -> None:
    created = client.post(URL, json={"identity": {"first_name": "Alba", "age": 30}}).json()
    card = client.get(f"{URL}/{created['id']}").json()["card"]
    assert card["identity"]["grammatical_gender"] == "non_indicato"
    assert card["traits"] == []
    assert (card["communication"]["address_form"], card["communication"]["narration"]) == ("tu", "nessuna")
    assert (card["communication"]["emoji"], card["communication"]["examples"]) == ("raramente", [])
    assert (card["visual"]["subject_tag"], card["visual"]["outfits"], card["visual"]["seed_policy"]) == (
        "1other",
        [],
        "random",
    )


def test_a_full_card_is_stored_and_read_back_whole(client: TestClient) -> None:
    detail = client.get(f"{URL}/{create_full(client)}").json()
    assert (detail["full_name"], detail["status"], detail["current_version_no"]) == ("Nerea Falchi", "active", 1)
    card = detail["card"]
    assert card["identity"] == FULL_CARD["identity"]
    assert card["traits"] == FULL_CARD["traits"]
    assert card["communication"] == FULL_CARD["communication"]
    assert {key: card["visual"][key] for key in FULL_CARD["visual"]} == FULL_CARD["visual"]


def test_the_card_survives_a_restart(settings: Settings, engine: Engine, database_path: Path) -> None:
    upgrade_to_head(engine)
    first = TestClient(create_app(settings, engine), base_url="http://localhost:4200")
    character_id = create_full(first)
    engine.dispose()
    reopened = create_database_engine(database_path)
    try:
        second = TestClient(create_app(settings, reopened), base_url="http://localhost:4200")
        card = second.get(f"{URL}/{character_id}").json()["card"]
        assert card["identity"] == FULL_CARD["identity"]
        assert card["communication"]["examples"] == FULL_CARD["communication"]["examples"]
        assert card["visual"]["outfits"] == FULL_CARD["visual"]["outfits"]
    finally:
        reopened.dispose()


def test_an_unknown_character_is_not_found(client: TestClient) -> None:
    response = client.get(f"{URL}/missing")
    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


def test_an_age_under_18_is_refused_by_the_api_and_by_the_database(client: TestClient, engine: Engine) -> None:
    for body in ({"identity": {"first_name": "Alba", "age": 17}}, {"first_name": "Alba", "age": 0}):
        response = client.post(URL, json=body)
        assert (response.status_code, response.json()["detail"]) == (422, "L'età deve essere almeno 18.")
    assert client.get(URL).json()["items"] == []
    with engine.begin() as connection, pytest.raises(IntegrityError, match="CHECK"):
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, created_at, updated_at) "
                "VALUES ('x', 'Alba', 17, '2026-11-07T10:00:00.000Z', '2026-11-07T10:00:00.000Z')"
            )
        )


def test_the_database_refuses_a_gender_outside_the_three_values(client: TestClient, engine: Engine) -> None:
    with engine.begin() as connection, pytest.raises(IntegrityError, match="CHECK"):
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, grammatical_gender, created_at, updated_at) "
                "VALUES ('x', 'Alba', 30, 'altro', '2026-11-07T10:00:00.000Z', '2026-11-07T10:00:00.000Z')"
            )
        )


def with_card(section: str, value: object) -> dict[str, object]:
    return {"identity": {"first_name": "Alba", "age": 30}, section: value}


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (with_card("communication", {"address_form": "voi"}), "Il campo «communication.address_form» non è valido."),
        (with_card("communication", {"narration": "molta"}), "Il campo «communication.narration» non è valido."),
        (with_card("communication", {"emoji": "sempre"}), "Il campo «communication.emoji» non è valido."),
        (
            with_card("communication", {"examples": [{"user": "a", "character": ""}]}),
            "Il campo «communication.examples.0.character» non è valido.",
        ),
        (
            with_card("communication", {"examples": [{"character": "ok"}] * 9}),
            "Il campo «communication.examples» non è valido.",
        ),
        (with_card("communication", {"avoid": ["x"] * 9}), "Il campo «communication.avoid» non è valido."),
        (with_card("communication", {"avoid": ["x" * 121]}), "Il campo «communication.avoid.0» non è valido."),
        (
            with_card("visual", {"identity_en": {"hair_color": "teal hair"}}),
            "Il valore di «visual.identity_en.hair_color» non è nel vocabolario dei tag.",
        ),
        (
            with_card("visual", {"identity_en": {"face": ["scar"]}}),
            "Il valore di «visual.identity_en.face» non è nel vocabolario dei tag.",
        ),
        (
            with_card("visual", {"identity_en": {"extra": ["one two three four five"]}}),
            "Ogni tag di «visual.identity_en.extra» può avere al massimo 4 parole.",
        ),
        (
            with_card("visual", {"outfits": [{"key": "usual", "label": "Solito"}]}),
            "La chiave di un abito non può essere «usual» né «scene».",
        ),
        (
            with_card("visual", {"outfits": [{"key": "scene", "label": "Scena"}]}),
            "La chiave di un abito non può essere «usual» né «scene».",
        ),
        (
            with_card("visual", {"outfits": [{"key": f"a{n}", "label": "A"} for n in range(6)]}),
            "Il campo «visual.outfits» non è valido.",
        ),
        (
            with_card("visual", {"outfits": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]}),
            "Due abiti non possono avere la stessa chiave.",
        ),
        (with_card("visual", {"locked_fields": ["voice"]}), "Il campo «visual.locked_fields.0» non è valido."),
        (with_card("visual", {"seed_policy": "fixed"}), "Con questa politica del seed serve un valore iniziale."),
        (with_card("colour", {}), "Il campo «colour» non è previsto."),
    ],
)
def test_the_voice_and_wardrobe_fields_refuse_invalid_values(
    client: TestClient, body: dict[str, object], detail: str
) -> None:
    response = client.post(URL, json=body)
    assert (response.status_code, response.json()["code"], response.json()["detail"]) == (
        422,
        "VALIDATION_ERROR",
        detail,
    )
    assert client.get(URL).json()["items"] == []


def test_slot_and_free_tags_are_stored_sanitized(client: TestClient) -> None:
    visual = {
        "identity_en": {
            "hair_color": " Red_Hair ",
            "face": ["Freckles", "freckles"],
            "extra": ["(Brass:1.5) pendant", ""],
        },
        "usual_outfit_en": ["embedding:evil jacket", "Canvas (jacket)"],
        "outfits": [{"key": "festa", "label": "Festa", "tags_en": ["blue\\dress", "[ribbon]"]}],
    }
    created = client.post(URL, json=with_card("visual", visual)).json()
    stored = client.get(f"{URL}/{created['id']}").json()["card"]["visual"]
    assert stored["identity_en"]["hair_color"] == "red hair"
    assert stored["identity_en"]["face"] == ["freckles"]
    assert stored["identity_en"]["extra"] == ["brass1.5 pendant"]
    assert stored["usual_outfit_en"] == ["jacket", "canvas jacket"]
    assert stored["outfits"][0]["tags_en"] == ["bluedress", "ribbon"]


def test_a_character_made_before_the_card_reads_with_a_default_profile(client: TestClient, engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, created_at, updated_at) "
                "VALUES ('old', 'Vecchio', 40, '2026-11-07T10:00:00.000Z', '2026-11-07T10:00:00.000Z')"
            )
        )
    card = client.get(f"{URL}/old").json()["card"]
    assert (card["identity"]["first_name"], card["visual"]["subject_tag"], card["communication"]["emoji"]) == (
        "Vecchio",
        "1other",
        "raramente",
    )


def edited(**identity: object) -> dict[str, Any]:
    return {**FULL_CARD, "identity": {**FULL_CARD["identity"], **identity}}


def versions(client: TestClient, character_id: str) -> list[dict[str, Any]]:
    response = client.get(f"{URL}/{character_id}/versions")
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def test_creating_a_character_records_version_1(client: TestClient) -> None:
    items = versions(client, create_full(client))
    assert [(item["version_no"], item["actor"], item["change_summary"]) for item in items] == [(1, "user", "Creazione")]


def test_an_edit_creates_a_version_and_a_save_with_no_change_creates_none(client: TestClient) -> None:
    """T-CHR-03."""
    character_id = create_full(client)
    same = client.put(f"{URL}/{character_id}", json=FULL_CARD)
    assert (same.status_code, same.json()["current_version_no"]) == (200, 1)
    assert len(versions(client, character_id)) == 1

    changed = client.put(f"{URL}/{character_id}", json=edited(age=28, occupation="Cartografa di corte"))
    assert (changed.status_code, changed.json()["current_version_no"]) == (200, 2)
    assert changed.json()["card"]["identity"]["age"] == 28
    assert client.get(f"{URL}/{character_id}").json()["card"]["identity"]["occupation"] == "Cartografa di corte"
    latest = versions(client, character_id)[0]
    assert (latest["version_no"], latest["actor"], latest["change_summary"]) == (2, "user", "Modificati: identità")

    repeated = client.put(f"{URL}/{character_id}", json=edited(age=28, occupation="Cartografa di corte"))
    assert repeated.json()["current_version_no"] == 2
    assert len(versions(client, character_id)) == 2


def test_a_save_accepts_the_flat_form_and_refuses_what_create_refuses(client: TestClient) -> None:
    character_id = str(client.post(URL, json={"first_name": "Alba", "age": 30}).json()["id"])
    flat = client.put(f"{URL}/{character_id}", json={"first_name": "Alba", "last_name": "Rossi", "age": 31})
    assert (flat.status_code, flat.json()["full_name"], flat.json()["current_version_no"]) == (200, "Alba Rossi", 2)
    refused = client.put(f"{URL}/{character_id}", json={"first_name": "Alba", "age": 17})
    assert (refused.status_code, refused.json()["detail"]) == (422, "L'età deve essere almeno 18.")
    assert client.get(f"{URL}/{character_id}").json()["current_version_no"] == 2
    missing = client.put(f"{URL}/missing", json={"first_name": "Alba", "age": 30})
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")


def test_a_save_replaces_traits_and_visual_profile_whole(client: TestClient) -> None:
    character_id = create_full(client)
    card = {**FULL_CARD, "traits": [{"category": "humor", "description": "Asciutto."}], "visual": {"hair": "Neri"}}
    stored = client.put(f"{URL}/{character_id}", json=card).json()["card"]
    assert stored["traits"] == [{"category": "humor", "description": "Asciutto.", "locked": False}]
    assert stored["visual"]["hair"] == "Neri"
    assert (stored["visual"]["outfits"], stored["visual"]["locked_fields"]) == ([], [])


def test_the_version_list_the_diff_and_a_restore_that_adds_a_version(client: TestClient) -> None:
    """T-CHR-08."""
    character_id = create_full(client)
    client.put(f"{URL}/{character_id}", json=edited(age=28))
    third = {**edited(age=29), "traits": [{"category": "humor", "description": "Asciutto."}]}
    client.put(f"{URL}/{character_id}", json=third)
    assert [item["version_no"] for item in versions(client, character_id)] == [3, 2, 1]

    snapshot = client.get(f"{URL}/{character_id}/versions/1").json()
    assert (snapshot["version_no"], snapshot["card"]["identity"]["age"]) == (1, 27)

    diff = client.get(f"{URL}/{character_id}/versions/1/diff/3").json()
    assert (diff["from_version_no"], diff["to_version_no"]) == (1, 3)
    assert [(change["field"], change["before"], change["after"]) for change in diff["changes"]] == [
        ("identity.age", 27, 29),
        ("traits", FULL_CARD["traits"], [{"category": "humor", "description": "Asciutto.", "locked": False}]),
    ]
    assert client.get(f"{URL}/{character_id}/versions/2/diff/2").json()["changes"] == []

    restored = client.post(f"{URL}/{character_id}/versions/1/restore")
    assert (restored.status_code, restored.json()["current_version_no"]) == (200, 4)
    assert restored.json()["card"]["identity"]["age"] == 27
    assert restored.json()["card"]["traits"] == FULL_CARD["traits"]
    items = versions(client, character_id)
    latest = items[0]
    assert (latest["version_no"], latest["actor"]) == (4, "restore")
    assert latest["change_summary"] == "Ripristinata la versione 1"
    assert len(items) == 4
    assert client.get(f"{URL}/{character_id}/versions/3").json()["card"]["identity"]["age"] == 29


def test_unknown_characters_and_versions_are_not_found(client: TestClient) -> None:
    character_id = create_full(client)
    for method, path in (
        ("get", f"{URL}/missing/versions"),
        ("get", f"{URL}/{character_id}/versions/9"),
        ("get", f"{URL}/{character_id}/versions/1/diff/9"),
        ("get", f"{URL}/missing/versions/1"),
        ("post", f"{URL}/{character_id}/versions/9/restore"),
        ("post", f"{URL}/missing/versions/1/restore"),
    ):
        response = client.request(method, path)
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND"), path
    assert len(versions(client, character_id)) == 1


def test_a_locked_trait_and_field_survive_a_proposal_but_not_the_users_own_edit(
    client: TestClient, engine: Engine
) -> None:
    """T-CHR-09 through the service and the database; the pure rules are in tests/unit/test_card_locks.py."""
    character_id = create_full(client)
    service = CharacterService(engine)
    proposal = {
        **FULL_CARD,
        "traits": [{"category": "humor", "description": "Asciutto."}],
        "visual": {
            "hair": "Neri",
            "eyes": "Grigi",
            "face": "Viso nuovo.",
            "identity_en": {"hair_color": "black hair", "eye_color": "grey eyes"},
        },
    }
    saved = service.apply_proposal(character_id, proposal)
    assert saved.current_version_no == 2
    assert [(trait.description, trait.locked) for trait in saved.card.traits] == [
        ("Perdersi in un posto noto.", True),
        ("Asciutto.", False),
    ]
    visual = saved.card.visual
    assert (visual.hair, visual.eyes, visual.face) == ("Capelli rossi corti.", "", "Viso nuovo.")
    assert (visual.identity_en.hair_color, visual.identity_en.eye_color) == ("red hair", "green eyes")
    assert visual.locked_fields == ["hair", "eyes"]
    assert versions(client, character_id)[0]["actor"] == "proposal"

    unchanged = service.apply_proposal(character_id, proposal)
    assert unchanged.current_version_no == 2

    unlocked = {**proposal, "visual": {**proposal["visual"], "locked_fields": []}}
    edited_by_user = client.put(f"{URL}/{character_id}", json=unlocked).json()["card"]
    assert (edited_by_user["visual"]["hair"], edited_by_user["traits"][0]["description"]) == (
        "Neri",
        "Asciutto.",
    )


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


def test_the_vocabulary_route_lists_every_tag_with_its_italian_aliases(client: TestClient) -> None:
    response = client.get(f"{URL}/vocabulary")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {"tag": "black hair", "category": "hair_color", "aliases_it": ["capelli neri", "nero"]} in items
    assert {item["category"] for item in items} >= {"hair_color", "eye_color", "skin", "hairstyle", "accessories"}
    assert not {item["category"] for item in items} & {"expression", "pose", "lighting"}
    assert all(item["aliases_it"] for item in items)


API = "/api/v1"


def table_count(engine: Engine, table: str, where: str = "1 = 1", **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params).scalar_one())


def chat_with_messages(client: TestClient, character_id: str) -> str:
    chat = client.post(f"{API}/conversations", json={"character_id": character_id}).json()["id"]
    response = client.post(f"{API}/conversations/{chat}/messages", json={"content": "Ciao"})
    assert response.status_code == 202, response.text
    return str(chat)


def listed_chats(client: TestClient, **query: str) -> list[str]:
    return [item["id"] for item in client.get(f"{API}/conversations", params=query).json()["items"]]


def test_duplicate_copies_the_card_but_not_conversations_or_memories(client: TestClient, engine: Engine) -> None:
    """T-CHR-04."""
    source = create_full(client)
    client.put(f"{URL}/{source}", json=edited(age=28))
    chat_with_messages(client, source)

    response = client.post(f"{URL}/{source}/duplicate")
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["id"] != source
    assert (copy["first_name"], copy["status"]) == ("Nerea (copia)", "active")

    original = client.get(f"{URL}/{source}").json()["card"]
    copied = client.get(f"{URL}/{copy['id']}").json()
    assert copied["current_version_no"] == 1
    assert copied["card"]["identity"] == {**original["identity"], "first_name": "Nerea (copia)"}
    for section in ("traits", "communication", "visual"):
        assert copied["card"][section] == original[section], section
    history = versions(client, copy["id"])
    assert [(item["version_no"], item["actor"]) for item in history] == [(1, "duplicate")]
    assert history[0]["change_summary"] == "Duplicato da Nerea"

    assert listed_chats(client, character_id=copy["id"]) == []
    assert table_count(engine, "conversations", "character_id = :id", id=copy["id"]) == 0
    assert table_count(engine, "conversations", "character_id = :id", id=source) == 1
    assert client.post(f"{URL}/missing/duplicate").status_code == 404


def test_duplicate_keeps_a_long_first_name_within_its_limit(client: TestClient) -> None:
    created = client.post(URL, json={"first_name": "A" * 80, "age": 30}).json()
    copy = client.post(f"{URL}/{created['id']}/duplicate").json()
    assert len(copy["first_name"]) == 80
    assert copy["first_name"].endswith(" (copia)")


def test_archive_hides_a_character_and_its_conversations_and_unarchive_brings_them_back(client: TestClient) -> None:
    character_id = create_full(client)
    chat = chat_with_messages(client, character_id)

    archived = client.post(f"{URL}/{character_id}/archive")
    assert (archived.status_code, archived.json()["status"]) == (200, "archived")
    assert client.post(f"{URL}/{character_id}/archive").json()["status"] == "archived"
    assert client.get(URL).json()["items"] == []
    assert [item["id"] for item in client.get(URL, params={"status": "archived"}).json()["items"]] == [character_id]
    assert listed_chats(client) == []
    # The character's own screen can still list them, and nothing was deleted.
    assert listed_chats(client, character_id=character_id) == [chat]
    assert client.get(f"{URL}/{character_id}").json()["status"] == "archived"
    assert client.post(f"{API}/conversations", json={"character_id": character_id}).status_code == 409

    restored = client.post(f"{URL}/{character_id}/unarchive")
    assert (restored.status_code, restored.json()["status"]) == (200, "active")
    assert [item["id"] for item in client.get(URL).json()["items"]] == [character_id]
    assert listed_chats(client) == [chat]
    for action in ("archive", "unarchive"):
        assert client.post(f"{URL}/missing/{action}").status_code == 404


def test_purge_deletes_the_character_with_its_data_after_the_name_is_typed(client: TestClient, engine: Engine) -> None:
    character_id = create_full(client)
    other = client.post(URL, json={"first_name": "Marco", "age": 30}).json()["id"]
    chat = chat_with_messages(client, character_id)
    other_chat = chat_with_messages(client, other)
    client.put(f"{URL}/{character_id}", json=edited(age=28))
    messages = table_count(engine, "messages", "conversation_id = :id", id=chat)
    assert messages >= 2

    preview = client.get(f"{URL}/{character_id}/purge-preview")
    assert (preview.status_code, preview.json()) == (200, {"conversations": 1, "messages": messages, "versions": 2})

    for wrong in ("", "nerea", "Marco"):
        refused = client.post(f"{URL}/{character_id}/purge", json={"confirm_name": wrong})
        assert (refused.status_code, refused.json()["code"]) == (422, "VALIDATION_ERROR"), wrong
    assert client.post(f"{URL}/{character_id}/purge", json={}).status_code == 422
    assert client.get(f"{URL}/{character_id}").status_code == 200
    assert table_count(engine, "messages", "conversation_id = :id", id=chat) == messages

    done = client.post(f"{URL}/{character_id}/purge", json={"confirm_name": " Nerea "})
    assert (done.status_code, done.content) == (204, b"")
    assert client.get(f"{URL}/{character_id}").status_code == 404
    assert client.get(f"{URL}/{character_id}/purge-preview").status_code == 404
    assert client.get(f"{API}/conversations/{chat}").status_code == 404
    for table, column in (
        ("characters", "id"),
        ("character_traits", "character_id"),
        ("character_visual_profiles", "character_id"),
        ("character_versions", "character_id"),
        ("conversations", "character_id"),
        ("generation_jobs", "character_id"),
    ):
        assert table_count(engine, table, f"{column} = :id", id=character_id) == 0, table
    assert table_count(engine, "messages", "conversation_id = :id", id=chat) == 0
    assert table_count(engine, "message_variants", "message_id NOT IN (SELECT id FROM messages)") == 0
    assert table_count(engine, "message_fts", "conversation_id = :id", id=chat) == 0
    assert table_count(engine, "generation_jobs", "conversation_id = :id", id=chat) == 0
    # Another character and its chat are untouched.
    assert client.get(f"{URL}/{other}").status_code == 200
    assert table_count(engine, "messages", "conversation_id = :id", id=other_chat) >= 2
    assert table_count(engine, "generation_jobs", "character_id = :id", id=other) >= 1
    assert client.post(f"{URL}/{character_id}/purge", json={"confirm_name": "Nerea"}).status_code == 404


def test_purge_waits_while_a_job_of_the_character_is_running(client: TestClient, engine: Engine) -> None:
    character_id = create_full(client)
    chat_with_messages(client, character_id)

    def mark_running(connection: Connection) -> None:
        connection.execute(
            text("UPDATE generation_jobs SET status = 'running' WHERE character_id = :id"), {"id": character_id}
        )

    write(engine, mark_running)
    refused = client.post(f"{URL}/{character_id}/purge", json={"confirm_name": "Nerea"})
    assert (refused.status_code, refused.json()["code"]) == (409, "CONFLICT")
    assert client.get(f"{URL}/{character_id}").status_code == 200
