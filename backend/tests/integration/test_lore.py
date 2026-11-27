"""Checks LoreService on a real database (P12.1a): T-LOR-04 (integration half) and the stemmed keys of saved entries."""

from typing import Any

import pytest
from sqlalchemy import Engine, text

from lyrveil.domain.errors import ConflictError, ContentPolicyBlockedError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id
from lyrveil.domain.lore import BookKind, BookStatus, KeyStems
from lyrveil.jobs.scheduler import read, write
from lyrveil.memory.text import stems
from lyrveil.persistence import lore as repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.lore import LoreService

NOW = "2026-11-27T10:00:00.000Z"
MINOR_TEXT = "Una bambina di sette anni abita al faro."


@pytest.fixture
def service(engine: Engine) -> LoreService:
    upgrade_to_head(engine)
    return LoreService(engine)


def add_character(engine: Engine, first_name: str = "Alba", language: str = "it") -> str:
    character_id = new_id()
    write(
        engine,
        lambda connection: connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, primary_language, status, created_at, updated_at) "
                "VALUES (:id, :name, 30, :language, 'active', :now, :now)"
            ),
            {"id": character_id, "name": first_name, "language": language, "now": NOW},
        ),
    )
    return character_id


def count(engine: Engine, table: str) -> int:
    return int(read(engine, lambda connection: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()))


def test_a_world_keeps_its_fields_and_its_defaults(service: LoreService) -> None:
    world = service.create_world({"name": "  Aldara ", "description": "Un regno di mare."})
    assert (world.kind, world.name, world.scan_depth, world.token_budget, world.status) == (
        BookKind.WORLD,
        "Aldara",
        4,
        None,
        BookStatus.ACTIVE,
    )
    assert (world.character_id, world.entry_count, world.source) == (None, 0, {})
    edited = service.update_book(world.id, {"name": "Aldara II", "scan_depth": 20, "token_budget": 900})
    assert (edited.name, edited.scan_depth, edited.token_budget, edited.description) == ("Aldara II", 20, 900, "")
    assert [book.id for book in service.list_worlds()] == [world.id]
    assert service.set_archived(world.id, True).status is BookStatus.ARCHIVED
    assert service.list_worlds() == []
    assert [book.id for book in service.list_worlds(BookStatus.ARCHIVED)] == [world.id]


def test_a_world_that_breaks_a_limit_or_the_content_guard_is_not_stored(service: LoreService, engine: Engine) -> None:
    with pytest.raises(ValidationFailedError):
        service.create_world({"name": "Mondo", "scan_depth": 21})
    with pytest.raises(ValidationFailedError):
        service.create_world({"name": "Mondo", "token_budget": 49})
    with pytest.raises(ContentPolicyBlockedError):
        service.create_world({"name": "Il regno delle bambine"})
    assert count(engine, "lore_books") == 0


def test_a_character_gets_one_own_book_named_after_it(service: LoreService, engine: Engine) -> None:
    character_id = add_character(engine, "Alba")
    book = service.own_book(character_id)
    assert (book.kind, book.character_id, book.name, book.scan_depth) == (BookKind.CHARACTER, character_id, "Alba", 4)
    assert service.own_book(character_id).id == book.id
    assert count(engine, "lore_books") == 1
    with pytest.raises(NotFoundError):
        service.own_book("missing")
    with pytest.raises(ConflictError):
        service.set_archived(book.id, True)
    with pytest.raises(ConflictError):
        service.delete_world(book.id)


def test_an_entry_keeps_its_key_text_and_its_stems(service: LoreService) -> None:
    world = service.create_world({"name": "Aldara"})
    saved = service.create_entry(
        world.id,
        {
            "title": "Il faro",
            "content": "Sorge a nord.",
            "keys": ["Le torri di Aldara", "faro"],
            "exclude_keys": ["Lune"],
        },
    )
    assert saved.keys == ("Le torri di Aldara", "faro")
    assert saved.exclude_keys == ("Lune",)
    assert saved.stems == KeyStems(
        language="it",
        keys=(tuple(stems("Le torri di Aldara", "it")), tuple(stems("faro", "it"))),
        exclude_keys=(tuple(stems("Lune", "it")),),
    )
    assert (saved.enabled, saved.constant, saved.case_sensitive, saved.priority, saved.insertion_order) == (
        True,
        False,
        False,
        0,
        100,
    )
    assert service.get_entry(saved.id) == saved
    assert service.get_book(world.id).entry_count == 1


def test_a_key_with_no_stem_is_refused_and_nothing_is_stored(service: LoreService, engine: Engine) -> None:
    world = service.create_world({"name": "Aldara"})
    with pytest.raises(ValidationFailedError, match="lui"):
        service.create_entry(world.id, {"content": "Un re.", "keys": ["lui"]})
    assert count(engine, "lore_entries") == 0
    kept = service.create_entry(world.id, {"content": "Un re.", "keys": ["re"]})
    with pytest.raises(ValidationFailedError):
        service.update_entry(kept.id, {"content": "Un re.", "keys": ["lui"]})
    assert service.get_entry(kept.id).keys == ("re",)


def test_saving_again_stems_the_keys_again_and_an_own_book_uses_the_characters_language(
    service: LoreService, engine: Engine
) -> None:
    character_id = add_character(engine, "Alba", language="en")
    own = service.own_book(character_id)
    saved = service.create_entry(own.id, {"content": "A tall tower.", "keys": ["towers"]})
    assert saved.stems.language == "en"
    assert saved.stems.keys == (tuple(stems("towers", "en")),)
    changed = service.update_entry(saved.id, {"content": "A tall tower.", "keys": ["lighthouse"]})
    assert changed.stems.keys == (tuple(stems("lighthouse", "en")),)
    assert changed.created_at == saved.created_at


def test_a_title_or_content_that_describes_a_minor_is_refused_and_nothing_is_stored(
    service: LoreService, engine: Engine
) -> None:
    world = service.create_world({"name": "Aldara"})
    with pytest.raises(ContentPolicyBlockedError):
        service.create_entry(world.id, {"content": MINOR_TEXT})
    with pytest.raises(ContentPolicyBlockedError):
        service.create_entry(world.id, {"title": "La bambina del faro", "content": "Un faro."})
    ok = service.create_entry(world.id, {"content": "Un faro."})
    with pytest.raises(ContentPolicyBlockedError):
        service.update_entry(ok.id, {"content": MINOR_TEXT})
    assert service.get_entry(ok.id).content == "Un faro."
    assert count(engine, "lore_entries") == 1


def test_an_entry_over_its_block_maximum_is_stored_and_flagged_as_never_inserted(service: LoreService) -> None:
    world = service.create_world({"name": "Aldara"})
    long_text = "x" * 8000
    over = service.create_entry(world.id, {"content": long_text, "keys": ["faro"]})
    assert over.never_inserted is True
    assert service.get_entry(over.id).content == long_text
    # About 670 estimated tokens: over block 11's maximum of 600, within block 12's maximum of 900.
    middle = {"content": "x" * 2000}
    assert service.create_entry(world.id, {**middle, "constant": True}).never_inserted is True
    assert service.create_entry(world.id, middle).never_inserted is False
    assert service.create_entry(world.id, {"content": "Un faro."}).never_inserted is False


def test_a_book_holds_at_most_500_entries(service: LoreService, engine: Engine) -> None:
    world = service.create_world({"name": "Aldara"})
    columns: dict[str, Any] = {
        "title": "",
        "content": "Un faro.",
        "keys_json": "[]",
        "secondary_keys_json": "[]",
        "exclude_keys_json": "[]",
        "keys_stem_json": "{}",
        "case_sensitive": 0,
        "constant": 0,
        "enabled": 1,
        "priority": 0,
        "insertion_order": 100,
        "note": "",
    }

    def fill(connection: Any) -> None:
        for _ in range(499):
            repository.insert_entry(connection, new_id(), world.id, columns, NOW)

    write(engine, fill)
    service.create_entry(world.id, {"content": "La cinquecentesima."})
    assert service.get_book(world.id).entry_count == 500
    with pytest.raises(ConflictError, match="500"):
        service.create_entry(world.id, {"content": "Una in più."})
    assert count(engine, "lore_entries") == 500
    other = service.create_world({"name": "Altro"})
    service.create_entry(other.id, {"content": "Un'altra."})


def test_deleting_a_world_deletes_its_entries_and_links_and_a_purged_character_takes_its_book(
    service: LoreService, engine: Engine
) -> None:
    character_id = add_character(engine)
    own = service.own_book(character_id)
    service.create_entry(own.id, {"content": "Una torre."})
    world = service.create_world({"name": "Aldara"})
    entry = service.create_entry(world.id, {"content": "Un faro."})

    def link(connection: Any) -> None:
        connection.execute(
            text("INSERT INTO lore_links (id, book_id, character_id, created_at) VALUES ('l', :book, :c, :now)"),
            {"book": world.id, "c": character_id, "now": NOW},
        )

    write(engine, link)
    assert [item.character_id for item in read(engine, lambda c: repository.list_links(c, world.id))] == [character_id]
    service.delete_world(world.id)
    with pytest.raises(NotFoundError):
        service.get_entry(entry.id)
    assert (count(engine, "lore_links"), count(engine, "lore_books"), count(engine, "lore_entries")) == (0, 1, 1)
    write(engine, lambda c: c.execute(text("DELETE FROM characters WHERE id = :id"), {"id": character_id}))
    assert (count(engine, "lore_books"), count(engine, "lore_entries")) == (0, 0)
