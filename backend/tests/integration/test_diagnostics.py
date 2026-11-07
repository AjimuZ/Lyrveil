"""Diagnostic mode (P04.9b, T-MEM-21 diagnostics): the prompt assembly of a delivered reply is stored, shown through the
API only while `app.diagnostic_mode` is on, keeps the prompt text only for replies built while it was on, is trimmed to
the newest 200 per conversation, and no message text reaches the logs.

The reply is produced by the real ChatInferenceService and ContextBuilder on a migrated database; only the text runtime
is the fake of test_reply_streaming.
"""

import io
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from integration.test_reply_streaming import MARKER, FakeRuntime, Ticks, new_chat, worker_for
from lyrveil.context.builder import BLOCK_NAMES
from lyrveil.domain.errors import NotFoundError
from lyrveil.events.hub import EventHub
from lyrveil.jobs.policy import JobStatus
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import configure_logging
from lyrveil.main import create_app
from lyrveil.persistence import assemblies
from lyrveil.persistence.assemblies import NewAssembly
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.services.diagnostics import DiagnosticsService
from lyrveil.services.memory_studio import MemoryStudioService
from lyrveil.services.messages import MessageService
from lyrveil.settings import Settings

API = "/api/v1"
CAT = "Ha un gatto di nome Briciola."


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    yield
    configure_logging("INFO")


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


async def reply_to(engine: Engine, conversation_id: str, content: str) -> str:
    """Sends `content`, lets the real reply handler answer it with a fake runtime, and returns the reply's id."""
    reply = MessageService(engine).send(conversation_id, content).reply
    _, scheduler = worker_for(engine, FakeRuntime(), EventHub("boot"), Ticks())
    finished = await scheduler.run_once()
    assert finished is not None
    assert finished.status is JobStatus.COMPLETED
    return reply.id


def assembly_count(engine: Engine, conversation_id: str) -> int:
    return read(
        engine,
        lambda connection: int(
            connection.execute(
                text("SELECT count(*) FROM prompt_assemblies WHERE conversation_id = :id"), {"id": conversation_id}
            ).scalar_one()
        ),
    )


@pytest.mark.anyio
async def test_a_reply_shows_its_stored_assembly_only_while_diagnostic_mode_is_on(
    migrated: Engine, settings: Settings
) -> None:
    buffer = io.StringIO()
    configure_logging("DEBUG", buffer)
    chat = new_chat(migrated)
    MemoryStudioService(migrated).create(chat, CAT, "character_fact", 4)
    client = TestClient(create_app(settings, migrated), base_url="http://localhost:4200")

    off = await reply_to(migrated, chat, f"Come sta Briciola il gatto? {MARKER}")
    assert client.get(f"{API}/settings").json() == {"app.diagnostic_mode": False}, "the documented default is off"
    assert client.get(f"{API}/messages/{off}/diagnostics").status_code == 404
    assert client.get(f"{API}/conversations/{chat}/diagnostics").status_code == 404
    assert assembly_count(migrated, chat) == 1, "the counts are recorded even while the mode is off"

    assert client.patch(f"{API}/settings", json={"app.diagnostic_mode": True}).json() == {"app.diagnostic_mode": True}
    assert client.patch(f"{API}/settings", json={"unknown.key": 1}).status_code == 422
    on = await reply_to(migrated, chat, f"Ricordi Briciola il gatto? {MARKER}")

    earlier = client.get(f"{API}/messages/{off}/diagnostics").json()
    assert earlier["prompt_text"] is None, "a reply built while the mode was off keeps no prompt text"
    shown = client.get(f"{API}/messages/{on}/diagnostics")
    assert shown.status_code == 200, shown.text
    diagnostics: dict[str, Any] = shown.json()
    assert (diagnostics["message_id"], diagnostics["conversation_id"]) == (on, chat)
    assert set(diagnostics["blocks"]) == set(BLOCK_NAMES)
    assert diagnostics["blocks"]["memories"] > 0
    budgets = diagnostics["budgets"]
    assert budgets["context_limit"] == diagnostics["context_limit"] == 8192
    assert budgets["reply_reserve"] == 120, "the card's default band `brevi`"
    assert budgets["available"] == 8192 - 120 - budgets["safety_margin"]
    assert (budgets["memory_target"], budgets["summary_target"]) == (900, 600)
    assert 0 < diagnostics["token_total"] <= budgets["available"]
    [used] = diagnostics["memories"]
    assert (used["content"], used["type"], used["kind"], used["label"]) == (CAT, "memory", "character_fact", "[sicuro]")
    assert used["score"] > 0
    assert used["token_cost"] > 0
    assert set(used["parts"]) == {
        "fts",
        "importance",
        "recency",
        "topic",
        "kind",
        "confidence",
        "status_factor",
        "locked_bonus",
        "previous_bonus",
    }
    assert diagnostics["summaries"] == []
    assert CAT in diagnostics["prompt_text"]
    assert MARKER in diagnostics["prompt_text"]

    listed = client.get(f"{API}/conversations/{chat}/diagnostics").json()["items"]
    assert [item["message_id"] for item in listed] == [on, off], "newest first"
    assert listed[0]["excerpt"].startswith("Risposta a ")
    assert client.get(f"{API}/messages/unknown/diagnostics").status_code == 404

    output = buffer.getvalue()
    assert "assembly_recorded" in output, "the record was not logged, so its path was not checked"
    assert MARKER not in output
    assert CAT not in output


@pytest.mark.anyio
async def test_turning_the_mode_off_hides_the_records_and_stops_keeping_prompt_text(migrated: Engine) -> None:
    chat = new_chat(migrated)
    service = DiagnosticsService(migrated)
    service.set_enabled(True)
    first = await reply_to(migrated, chat, "Ciao")
    assert service.reply(first)["prompt_text"] is not None
    service.set_enabled(False)
    second = await reply_to(migrated, chat, "Come stai?")
    with pytest.raises(NotFoundError):
        service.reply(first)
    service.set_enabled(True)
    assert service.reply(second)["prompt_text"] is None
    assert service.reply(first)["prompt_text"] is not None, "turning the mode off does not erase the earlier rows"


@pytest.mark.anyio
async def test_only_the_newest_two_hundred_assemblies_of_a_conversation_are_kept(migrated: Engine) -> None:
    chat = new_chat(migrated)
    reply = await reply_to(migrated, chat, "Ciao")
    row = read(
        migrated,
        lambda connection: connection.execute(
            text("SELECT active_variant_id FROM messages WHERE id = :id"), {"id": reply}
        ).scalar_one(),
    )

    def new(number: int) -> NewAssembly:
        return NewAssembly(f"extra-{number:04d}", chat, reply, row, {"blocks": {}}, [], [], number, 8192, None)

    def store(number: int) -> None:
        stamp = f"2099-01-01T00:{number // 60:02d}:{number % 60:02d}.000Z"
        write(migrated, lambda connection: assemblies.insert(connection, new(number), stamp))

    for number in range(assemblies.RETENTION + 5):
        store(number)
    assert assembly_count(migrated, chat) == assemblies.RETENTION
    kept = read(
        migrated,
        lambda connection: connection.execute(
            text("SELECT prompt_assembly_id FROM message_variants WHERE id = :id"), {"id": row}
        ).scalar_one(),
    )
    assert kept == f"extra-{assemblies.RETENTION + 4:04d}", "the variant points at its newest assembly"
    oldest = read(
        migrated,
        lambda connection: connection.execute(
            text("SELECT min(token_total) FROM prompt_assemblies WHERE conversation_id = :id"), {"id": chat}
        ).scalar_one(),
    )
    assert oldest == 5, "the first five extra rows and the reply's own were trimmed"
