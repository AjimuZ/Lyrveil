"""T-LOR-06 on a migrated database: each assembly stores `lore_refs`, diagnostic mode shows them apart from the
memories, a cut entry is recorded as cut with its block, and memory extraction never reads lore (ADR 0049,
Diagnostics).

The reply is produced by the real ChatInferenceService and ContextBuilder; the extraction job by the real
MemoryExtractionService. Only the text runtimes are the fakes of test_reply_streaming and test_memory_extraction.
"""

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from integration.test_diagnostics import reply_to
from integration.test_memory_extraction import VALID, Clock, Leases, ScriptedRuntime, enqueue, scheduler, seeded_chat
from integration.test_reply_streaming import new_chat
from lyrveil.jobs.policy import JobStatus
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import read
from lyrveil.main import create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.diagnostics import DiagnosticsService
from lyrveil.services.lore import LoreService
from lyrveil.settings import Settings

API = "/api/v1"
STABLE = "Il laboratorio di Alba è in via del Campo."


def words(first: str, count: int) -> str:
    """Returns `count` words starting with `first`; the fake runtimes count one token per word."""
    return " ".join([first, *["parola"] * (count - 1)])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


def character_of(engine: Engine, conversation_id: str) -> str:
    return read(
        engine,
        lambda connection: str(
            connection.execute(
                text("SELECT character_id FROM conversations WHERE id = :id"), {"id": conversation_id}
            ).scalar_one()
        ),
    )


@pytest.mark.anyio
async def test_each_assembly_stores_its_lore_entries_kept_and_cut_apart_from_the_memories(
    migrated: Engine, settings: Settings
) -> None:
    chat = new_chat(migrated)
    lore = LoreService(migrated)
    own = lore.own_book(character_of(migrated, chat))
    stable = lore.create_entry(own.id, {"title": "Il laboratorio", "content": STABLE, "constant": True})
    # A book budget of 50 tokens holds the first of the two keyed entries (32 tokens each) and cuts the second.
    world = lore.create_world({"name": "Genova", "token_budget": 50})
    lighthouse = lore.create_entry(
        world.id, {"title": "Faro", "content": words("faro", 30), "keys": ["faro"], "priority": 5}
    )
    harbour = lore.create_entry(world.id, {"title": "Porto", "content": words("porto", 30), "keys": ["porto"]})
    lore.add_link(world.id, conversation_id=chat)
    DiagnosticsService(migrated).set_enabled(True)

    reply = await reply_to(migrated, chat, "Parliamo del faro e del porto stasera")

    client = TestClient(create_app(settings, migrated), base_url="http://localhost:4200")
    shown = client.get(f"{API}/messages/{reply}/diagnostics")
    assert shown.status_code == 200, shown.text
    diagnostics: dict[str, Any] = shown.json()
    assert diagnostics["memories"] == [], "lore is listed apart from the memories"
    refs = diagnostics["lore_refs"]
    assert [(ref["title"], ref["status"]) for ref in refs] == [
        ("Il laboratorio", "kept"),
        ("Faro", "kept"),
        ("Porto", "cut"),
    ], "the kept entries first, then the cut ones"
    by_title = {ref["title"]: ref for ref in refs}

    constant = by_title["Il laboratorio"]
    assert (constant["entry_id"], constant["book_id"], constant["book_name"]) == (stable.id, own.id, own.name)
    assert (constant["block"], constant["matched_key"]) == ("lore_constant", None)
    assert constant["content_hash"] == hashlib.sha256(stable.content.encode("utf-8")).hexdigest()

    kept = by_title["Faro"]
    assert (kept["entry_id"], kept["book_id"], kept["book_name"]) == (lighthouse.id, world.id, "Genova")
    assert (kept["block"], kept["matched_key"]) == ("lore_activated", "faro")

    cut = by_title["Porto"]
    assert (cut["entry_id"], cut["block"], cut["matched_key"]) == (harbour.id, "lore_activated", "porto")
    assert cut["content_hash"] == hashlib.sha256(harbour.content.encode("utf-8")).hexdigest()

    prompt = diagnostics["prompt_text"]
    assert STABLE in prompt
    assert lighthouse.content in prompt
    assert harbour.content not in prompt, "a cut entry is not in the prompt"
    assert diagnostics["blocks"]["lore_constant"] > 0
    assert diagnostics["blocks"]["lore_activated"] > 0


@pytest.mark.anyio
async def test_an_assembly_without_lore_has_no_lore_refs(migrated: Engine) -> None:
    chat = new_chat(migrated)
    service = DiagnosticsService(migrated)
    service.set_enabled(True)

    reply = await reply_to(migrated, chat, "Ciao")

    assert service.reply(reply)["lore_refs"] == []


@pytest.mark.anyio
async def test_extraction_never_reads_lore(migrated: Engine) -> None:
    conversation_id, _ = seeded_chat(migrated)
    lore = LoreService(migrated)
    own = lore.own_book(character_of(migrated, conversation_id))
    lore.create_entry(
        own.id, {"title": "Segreto", "content": "Dario custodisce una chiave di ottone.", "constant": True}
    )
    world = lore.create_world({"name": "Milano"})
    lore.create_entry(
        world.id, {"title": "Gatto", "content": "Briciola è una gatta soriana del quartiere.", "keys": ["Briciola"]}
    )
    lore.add_link(world.id, conversation_id=conversation_id)
    clock = Clock()
    queue = JobQueue(clock=clock)
    enqueue(migrated, queue, conversation_id)
    runtime = ScriptedRuntime(VALID)

    finished = await scheduler(migrated, runtime, queue, clock, Leases()).run_once()

    assert finished is not None
    assert finished.status is JobStatus.COMPLETED
    [request] = runtime.requests
    assert "Briciola" in request.prompt, "the window names the key, so an activated entry would have been read"
    for lore_text in ("chiave di ottone", "gatta soriana", "Segreto", "Il tuo mondo"):
        assert lore_text not in request.prompt, lore_text
