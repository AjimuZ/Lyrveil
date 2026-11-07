"""T-SEC-07 at each character entry point, through the API on a real database: create, save, proposal, restore,
duplicate and import refuse text that describes a minor with 422 CONTENT_POLICY_BLOCKED and write nothing; the
database CHECK refuses an age under 18; and no deployment setting, environment variable or stored application setting
changes any of it (ADR 0018). The draft entry point is in test_character_drafts.py, the guard's cases in
tests/unit/test_content_guard.py."""

import json
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.errors import ContentPolicyBlockedError
from lyrveil.jobs.scheduler import read, write
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence import characters as repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.characters import CharacterService
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"
ADULT: dict[str, Any] = {
    "identity": {"first_name": "Nerea", "age": 27, "biography": "Cartografa in una città di porto."},
    "traits": [{"category": "main_trait", "description": "Ironica, curiosa.", "locked": False}],
}
MINOR_TEXT = "Una ragazzina timida."
BLOCKED_BIOGRAPHY = "Il campo «identity.biography» parla di un minore. La scheda descrive solo adulti."
BLOCKED_DESCRIPTION = "La descrizione indica un'età sotto i 18 anni. Lyrveil crea solo personaggi adulti."
NOW = "2026-11-07T10:00:00.000Z"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


@pytest.fixture
def client(settings: Settings, migrated: Engine, tmp_path: Path) -> TestClient:
    app = create_app(settings, migrated, media_store=MediaStore(tmp_path / "data" / "media"))
    return TestClient(app, base_url="http://localhost:4200")


def with_biography(biography: str) -> dict[str, Any]:
    card: dict[str, Any] = json.loads(json.dumps(ADULT))
    card["identity"]["biography"] = biography
    return card


def assert_blocked(response: Response, detail: str = BLOCKED_BIOGRAPHY) -> None:
    assert response.status_code == 422, response.text
    assert (response.json()["code"], response.json()["detail"]) == ("CONTENT_POLICY_BLOCKED", detail)


def create(client: TestClient) -> str:
    response = client.post(CHARACTERS, json=ADULT)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def count(client: TestClient) -> int:
    return len(client.get(CHARACTERS).json()["items"])


def import_document(client: TestClient, document: dict[str, Any]) -> Response:
    data = json.dumps(document).encode()
    files = {"file": ("c.lyrveil-character.json", data, "application/json")}
    response: Response = client.post(f"{CHARACTERS}/import", files=files)
    return response


def exported_with_biography(client: TestClient, biography: str) -> dict[str, Any]:
    """Returns the export of a new adult character whose biography is then replaced, as a third-party file would."""
    document: dict[str, Any] = client.get(f"{CHARACTERS}/{create(client)}/export").json()
    document["character"]["identity"]["biography"] = biography
    return document


def test_create_refuses_text_that_describes_a_minor_in_both_body_forms(client: TestClient) -> None:
    assert_blocked(client.post(CHARACTERS, json=with_biography(MINOR_TEXT)))
    flat = {"first_name": "Alba", "age": 30, "narrative_identity": "Ha 16 anni."}
    assert_blocked(
        client.post(CHARACTERS, json=flat),
        "Il campo «identity.narrative_identity» indica un'età sotto i 18 anni. La scheda descrive solo adulti.",
    )
    assert count(client) == 0


def test_a_save_is_refused_and_the_stored_card_stays(client: TestClient) -> None:
    character_id = create(client)
    changed: dict[str, Any] = json.loads(json.dumps(ADULT))
    changed["traits"][0]["description"] = "A volte si comporta da bambina."
    assert_blocked(
        client.put(f"{CHARACTERS}/{character_id}", json=changed),
        "Il campo «traits.0.description» parla di un minore. La scheda descrive solo adulti.",
    )
    stored = client.get(f"{CHARACTERS}/{character_id}").json()
    assert (stored["current_version_no"], stored["card"]["traits"][0]["description"]) == (1, "Ironica, curiosa.")
    assert len(client.get(f"{CHARACTERS}/{character_id}/versions").json()["items"]) == 1


def test_a_proposal_goes_through_the_same_guard_as_a_save(client: TestClient, migrated: Engine) -> None:
    character_id = create(client)
    service = CharacterService(migrated)
    with pytest.raises(ContentPolicyBlockedError) as refused:
        service.apply_proposal(character_id, with_biography("Frequenta il liceo."))
    assert refused.value.detail == BLOCKED_BIOGRAPHY
    assert service.get(character_id).current_version_no == 1


def plant(engine: Engine, character_id: str, card: CharacterCard, *, version_no: int, current: bool) -> None:
    """Writes `card` past the guard, as data stored before a list entry existed: as version `version_no`, and as the
    current card too when `current` is set."""

    def work(connection: Connection) -> None:
        repository.insert_version(
            connection,
            character_id=character_id,
            version_no=version_no,
            card=card,
            change_summary="Prima della lista",
            actor="user",
            now=NOW,
        )
        if current:
            repository.replace_card(connection, character_id=character_id, card=card, version_no=version_no, now=NOW)

    write(engine, work)


def test_restore_and_duplicate_refuse_an_old_card_that_describes_a_minor(client: TestClient, migrated: Engine) -> None:
    character_id = create(client)
    old = CharacterCard.model_validate(with_biography(MINOR_TEXT))
    plant(migrated, character_id, old, version_no=2, current=False)

    assert_blocked(client.post(f"{CHARACTERS}/{character_id}/versions/2/restore"))
    stored = client.get(f"{CHARACTERS}/{character_id}").json()
    assert (stored["current_version_no"], stored["card"]["identity"]["biography"]) == (
        1,
        ADULT["identity"]["biography"],
    )
    assert len(client.get(f"{CHARACTERS}/{character_id}/versions").json()["items"]) == 2, "restore appended nothing"

    plant(migrated, character_id, old, version_no=3, current=True)
    assert_blocked(client.post(f"{CHARACTERS}/{character_id}/duplicate"))
    assert count(client) == 1


def test_an_import_is_refused_and_creates_nothing(client: TestClient) -> None:
    document = exported_with_biography(client, MINOR_TEXT)
    assert_blocked(import_document(client, document))
    assert count(client) == 1


def test_the_database_refuses_an_age_under_18_on_insert_and_on_update(client: TestClient, migrated: Engine) -> None:
    character_id = create(client)
    with migrated.begin() as connection, pytest.raises(IntegrityError, match="CHECK"):
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, created_at, updated_at) "
                "VALUES ('x', 'Alba', 17, :now, :now)"
            ),
            {"now": NOW},
        )
    with migrated.begin() as connection, pytest.raises(IntegrityError, match="CHECK"):
        connection.execute(text("UPDATE characters SET age = 17 WHERE id = :id"), {"id": character_id})
    assert client.get(f"{CHARACTERS}/{character_id}").json()["card"]["identity"]["age"] == 27


HOSTILE_ENVIRONMENT = {
    "LYRVEIL_CONTENT_GUARD": "off",
    "LYRVEIL_CONTENT_GUARD_ENABLED": "false",
    "LYRVEIL_MIN_AGE": "0",
    "LYRVEIL_ALLOW_MINORS": "true",
    "LYRVEIL_EXPLICIT_MODE": "true",
}
# Rows of the application settings table that look like switches; no code reads them, and none may matter.
HOSTILE_APPLICATION_SETTINGS = {
    "content_guard.enabled": "false",
    "safety.allow_minors": "true",
    "characters.min_age": "0",
    "app.explicit_mode": "true",
}


def test_no_setting_or_configuration_changes_the_minor_safeguards(
    migrated: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in HOSTILE_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    with migrated.begin() as connection:
        for key, value in HOSTILE_APPLICATION_SETTINGS.items():
            connection.execute(
                text("INSERT INTO application_settings (key, value_json, updated_at) VALUES (:key, :value, :now)"),
                {"key": key, "value": value, "now": NOW},
            )
    # Every deployment setting away from its default.
    settings = Settings(
        control_token=SecretStr("cd" * 32),
        bind_address=IPv4Address("0.0.0.0"),
        http_port=8080,
        tz="UTC",
        log_level="DEBUG",
        allow_low_memory=True,
        model_licenses_accepted=True,
    )
    app = create_app(settings, migrated, media_store=MediaStore(tmp_path / "data" / "media"))
    client = TestClient(app, base_url="http://localhost:8080")

    under_age = client.post(CHARACTERS, json={"first_name": "Alba", "age": 17})
    assert (under_age.status_code, under_age.json()["code"]) == (422, "VALIDATION_ERROR")
    assert_blocked(client.post(CHARACTERS, json=with_biography(MINOR_TEXT)))
    assert_blocked(
        client.post(f"{CHARACTERS}/drafts", json={"description": "Crea una sedicenne timida."}), BLOCKED_DESCRIPTION
    )
    assert_blocked(import_document(client, exported_with_biography(client, MINOR_TEXT)))
    assert count(client) == 1, "only the adult the export needed exists"
    assert job_count(migrated) == 0


def job_count(engine: Engine) -> int:
    def work(connection: Connection) -> int:
        return int(connection.execute(text("SELECT count(*) FROM generation_jobs")).scalar_one())

    return read(engine, work)
