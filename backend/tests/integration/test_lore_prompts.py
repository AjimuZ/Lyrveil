"""T-LOR-02 on a migrated database: a reply reads the character's own book, the worlds linked to the character and the
worlds linked to its own conversation, a conversation started from a scenario reads its copies of the scenario's links,
and no link of another conversation applies (ADR 0049, Links).

The character Giulia has an own book and is linked to the world Genova. Her first chat is linked to the world Il faro
and to Genova again; her second chat is linked to Venezia; a third chat starts from a scenario linked to Il porto; the
world Torino is linked to nothing. Each prompt is built as ChatInferenceService loads it (load_context_request).
"""

import pytest
from sqlalchemy import Connection, Engine

from integration.test_context_builder import NOW, STAMP, add_message
from integration.test_persona_prompts import CARD, assemble
from lyrveil.domain.identifiers import new_id
from lyrveil.domain.messages import Author
from lyrveil.jobs.scheduler import write
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.conversations import ConversationService
from lyrveil.services.lore import LoreService
from lyrveil.services.scenarios import ScenarioService

OWN = "Il laboratorio: Giulia restaura mappe antiche in via del Campo."
GENOVA = "- Genova è una repubblica marinara."
LIGHTHOUSE = "- La Lanterna è il faro più alto del Mediterraneo."
VENEZIA = "Venezia ha centodiciotto isole."
PORT = "Il porto vecchio ha un acquario."
TORINO = "Torino ha la Mole."
QUESTION = "Andiamo a vedere il faro stasera?"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


def world(lore: LoreService, name: str, entry: dict[str, object]) -> str:
    book = lore.create_world({"name": name})
    lore.create_entry(book.id, entry)
    return book.id


def start(engine: Engine) -> dict[str, str]:
    """Writes the character, its books, links and three chats, and returns the chats' ids by name. Each chat ends with
    the user asking about the lighthouse, the key of the world linked to the first chat only."""
    character_id = new_id()

    def create(connection: Connection) -> None:
        character_repository.insert(connection, character_id=character_id, card=CARD, now=STAMP)

    write(engine, create)
    lore = LoreService(engine, clock=lambda: NOW)
    own = lore.own_book(character_id)
    lore.create_entry(
        own.id,
        {"title": "Il laboratorio", "content": "{{char}} restaura mappe antiche in via del Campo.", "constant": True},
    )
    scenario = ScenarioService(engine).create({"title": "Sera al porto"})
    conversations = ConversationService(engine)
    chats = {
        "first": conversations.create(character_id).id,
        "second": conversations.create(character_id).id,
        "scenario": "",
    }
    genova = world(lore, "Genova", {"content": GENOVA.removeprefix("- "), "constant": True})
    lore.add_link(genova, character_id=character_id)
    lore.add_link(genova, conversation_id=chats["first"])
    lighthouse = world(lore, "Il faro", {"content": LIGHTHOUSE.removeprefix("- "), "keys": ["faro"]})
    lore.add_link(lighthouse, conversation_id=chats["first"])
    lore.add_link(world(lore, "Venezia", {"content": VENEZIA, "constant": True}), conversation_id=chats["second"])
    lore.add_link(world(lore, "Il porto", {"content": PORT, "constant": True}), scenario_id=scenario.id)
    world(lore, "Torino", {"content": TORINO, "constant": True})
    chats["scenario"] = conversations.create(character_id, scenario_id=scenario.id).id
    for chat in chats.values():
        add_message(engine, chat, Author.USER, QUESTION)
    return chats


@pytest.mark.anyio
async def test_a_reply_reads_the_own_book_and_the_worlds_of_its_character_and_chat(migrated: Engine) -> None:
    chats = start(migrated)

    first = (await assemble(migrated, chats["first"], "gemma4")).prompt
    assert f"Il tuo mondo:\n- {OWN}\n{GENOVA}" in first.prefix, "the own book first, in the stable text"
    assert first.text.count(GENOVA) == 1, "a world linked to the character and to the chat is read once"
    dynamic = first.text[len(first.prefix) :]
    assert f"Cose che sai su quello di cui state parlando:\n{LIGHTHOUSE}" in dynamic
    assert LIGHTHOUSE not in first.prefix
    for other in (VENEZIA, PORT, TORINO):
        assert other not in first.text, other
    assert "{{" not in first.text

    second = (await assemble(migrated, chats["second"], "gemma4")).prompt.text
    assert OWN in second
    assert GENOVA in second
    assert VENEZIA in second
    for other in (LIGHTHOUSE, PORT, TORINO):
        assert other not in second, "no link of another conversation applies"

    from_scenario = (await assemble(migrated, chats["scenario"], "gemma4")).prompt.text
    assert OWN in from_scenario
    assert GENOVA in from_scenario
    assert PORT in from_scenario, "the copy of the scenario's link"
    for other in (LIGHTHOUSE, VENEZIA, TORINO):
        assert other not in from_scenario, other
