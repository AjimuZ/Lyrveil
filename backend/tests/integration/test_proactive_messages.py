"""T-SCH-04 and T-SCH-05 (integration side), and the delivery of a proactive message: ProactiveMessageService on a
migrated database with a fake text runtime.

Checks that `send: false` creates no message, that a generated message goes through the reply checks and is written with
its event's end, that the generation prompt has no message marker, that a `scheduled_message` skips the decision, that a
text too similar to a recent proactive message is regenerated once and then skipped, and how a failure ends the event.
"""

import dataclasses
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import Author, MessageStatus
from lyrveil.events.hub import EventHub
from lyrveil.jobs.handlers import JobDone, JobFailedError, RunContext
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import messages as repository
from lyrveil.persistence import settings as settings_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.scheduling import store
from lyrveil.scheduling.rules import EventKind, MissedPolicy
from lyrveil.services.conversations import ConversationService
from lyrveil.services.proactive_messages import ProactiveMessageService

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
ENABLED = json.dumps({"enabled": True})
EARLIER = "Ciao Utente, oggi ho pensato a te e al tuo colloquio. Com'è andata?"
FRESH = "Ti è venuto in mente il libro di cui parlavi sabato scorso?"


def decision(send: bool, reason: str = "Ieri aveva un colloquio.") -> str:
    return json.dumps({"schema_version": 1, "send": send, "reason": reason, "hook_ref": None})


class FakeRuntime:
    """A TextRuntime that answers each structured generation with the next of `decisions` and each stream with the next
    of `texts`, one word per token, and records every request."""

    def __init__(self, decisions: list[str], texts: list[str]) -> None:
        self.decisions = decisions
        self.texts = texts
        self.decision_requests: list[TextRequest] = []
        self.requests: list[TextRequest] = []
        self.aborted: list[str] = []

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        self.requests.append(request)
        words = self.texts.pop(0).split(" ")
        tokens = [word if index == 0 else f" {word}" for index, word in enumerate(words)]
        for token in tokens:
            yield TextChunk(token)
        yield TextChunk("", "stop", "".join(tokens))

    async def generate(self, request: TextRequest) -> str:
        self.decision_requests.append(request)
        return self.decisions.pop(0)

    async def abort(self, genkey: str) -> bool:
        self.aborted.append(genkey)
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


def new_chat(engine: Engine, *, enabled: bool = True) -> str:
    character_id = new_id()

    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO characters (id, first_name, age, status, created_at, updated_at) "
                "VALUES (:id, 'Alba', 30, 'active', :now, :now)"
            ),
            {"id": character_id, "now": to_utc_text(NOW)},
        )

    write(engine, work)
    stamp = to_utc_text(NOW)
    write(engine, lambda c: settings_repository.set_value(c, settings_repository.PROACTIVE_ENABLED, True, stamp))
    conversation_id = ConversationService(engine).create(character_id).id
    set_proactive(engine, conversation_id, ENABLED if enabled else None)
    return conversation_id


def set_proactive(engine: Engine, conversation_id: str, document: str | None) -> None:
    write(
        engine,
        lambda connection: connection.execute(
            text("UPDATE conversations SET proactive_json = :document WHERE id = :id"),
            {"document": document, "id": conversation_id},
        ),
    )


def add_proactive_message(engine: Engine, conversation_id: str, content: str) -> None:
    def work(connection: Connection) -> None:
        repository.insert_message(
            connection,
            message_id=new_id(),
            conversation_id=conversation_id,
            seq=repository.take_seq(connection, conversation_id, to_utc_text(NOW)),
            client_message_id=None,
            author=Author.CHARACTER,
            status=MessageStatus.COMPLETE,
            read_state=None,
            content=content,
            now=to_utc_text(NOW),
            scheduled_event_id=new_id(),
        )

    write(engine, work)


def add_running_event(
    engine: Engine, conversation_id: str, kind: EventKind = EventKind.CONTEXTUAL_INITIATIVE, note: str | None = None
) -> tuple[str, Job]:
    """Creates an event the scheduler has already started, with its queued job, as SchedulerService leaves them."""
    stamp = to_utc_text(NOW)

    def work(connection: Connection) -> tuple[str, Job]:
        character_id = connection.execute(
            text("SELECT character_id FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).scalar_one()
        event_id = store.insert_event(
            connection,
            conversation_id=conversation_id,
            character_id=character_id,
            kind=kind,
            due_at=stamp,
            note=note,
            missed_policy=MissedPolicy.SKIP,
            created_by="scheduler",
            now=stamp,
        )
        job = JobQueue(clock=lambda: NOW).enqueue(
            connection,
            JobKind.PROACTIVE_MESSAGE,
            {"event_id": event_id, "event_kind": kind.value},
            conversation_id=conversation_id,
            character_id=character_id,
        )
        store.start(connection, event_id, job.id, stamp)
        return event_id, job

    return write(engine, work)


def event_row(engine: Engine, event_id: str) -> tuple[str, str | None, str | None]:
    row = read(
        engine,
        lambda connection: connection.execute(
            text("SELECT status, outcome_reason, message_id FROM scheduled_events WHERE id = :id"), {"id": event_id}
        ).one(),
    )
    return row[0], row[1], row[2]


def proactive_messages(engine: Engine) -> list[tuple[str, str, str]]:
    rows = read(
        engine,
        lambda connection: connection.execute(
            text("SELECT content, author, status FROM messages WHERE scheduled_event_id IS NOT NULL ORDER BY seq")
        ).all(),
    )
    return [(row[0], row[1], row[2]) for row in rows]


def service(engine: Engine, runtime: FakeRuntime, hub: EventHub | None = None) -> ProactiveMessageService:
    return ProactiveMessageService(engine, runtime, hub or EventHub("boot"), clock=lambda: NOW)


async def run(handler: ProactiveMessageService, job: Job) -> dict[str, Any]:
    outcome = await handler(job, RunContext(lease=None, genkey="genkey-1"))
    assert isinstance(outcome, JobDone)
    assert outcome.result is not None
    return dict(outcome.result)


@pytest.mark.anyio
async def test_send_false_ends_the_event_skipped_and_creates_no_message(migrated: Engine) -> None:
    """T-SCH-04."""
    conversation_id = new_chat(migrated)
    event_id, job = add_running_event(migrated, conversation_id)
    runtime = FakeRuntime([decision(False, "Non c'è niente da dire.")], [])
    hub = EventHub("boot")

    result = await run(service(migrated, runtime, hub), job)

    assert result == {"status": "skipped", "reason": "Non c'è niente da dire."}
    assert event_row(migrated, event_id) == ("skipped", "Non c'è niente da dire.", None)
    assert read(migrated, lambda c: c.execute(text("SELECT count(*) FROM messages")).scalar_one()) == 0
    assert runtime.requests == []
    assert not hub.missed(0, hub.boot_id)


@pytest.mark.anyio
async def test_a_message_is_written_with_its_event_and_goes_through_the_reply_checks(migrated: Engine) -> None:
    conversation_id = new_chat(migrated)
    event_id, job = add_running_event(migrated, conversation_id)
    runtime = FakeRuntime([decision(True)], ["Ciao Utente, ti penso. Come stai?"])
    hub = EventHub("boot")

    result = await run(service(migrated, runtime, hub), job)

    assert result["status"] == "delivered"
    assert proactive_messages(migrated) == [("Ciao Utente, ti penso. Come stai?", "character", "complete")]
    status, reason, message_id = event_row(migrated, event_id)
    assert (status, reason, message_id) == ("delivered", None, result["message_id"])
    row = read(
        migrated,
        lambda c: c.execute(
            text(
                "SELECT m.scheduled_event_id, m.active_variant_id, v.status, v.checks_json FROM messages AS m "
                "JOIN message_variants AS v ON v.id = m.active_variant_id WHERE m.id = :id"
            ),
            {"id": message_id},
        ).one(),
    )
    assert row[0] == event_id
    assert row[2] == "complete"
    recorded = json.loads(row[3])
    assert set(recorded) == {"normalizer", "checks", "restarts", "regenerations"}
    assert recorded["regenerations"] == 0
    published = hub.missed(0, hub.boot_id)
    assert published is not None
    assert [event.type for event in published] == ["message.completed"]
    assert published[0].data["message"]["id"] == message_id


@pytest.mark.anyio
async def test_the_prompt_has_no_message_marker_and_ends_with_the_instruction_and_a_short_reserve(
    migrated: Engine,
) -> None:
    conversation_id = new_chat(migrated)
    _, job = add_running_event(migrated, conversation_id)
    runtime = FakeRuntime([decision(True, "Ieri aveva un colloquio.")], ["Ciao Utente, com'è andato il colloquio?"])

    await run(service(migrated, runtime), job)

    prompt = runtime.requests[0].prompt
    assert "[Contesto per Alba, non scritto da Utente]" in prompt
    assert "[Messaggio di" not in prompt
    frame = prompt.rsplit("[Contesto per", 1)[1]
    assert "Sei tu a scrivere a Utente: non stai rispondendo a un suo messaggio." in frame
    assert "Motivo: Ieri aveva un colloquio." in frame
    assert runtime.requests[0].max_length <= 120
    assert "Situazione:" in runtime.decision_requests[0].prompt


@pytest.mark.anyio
async def test_a_scheduled_message_skips_the_decision_and_uses_its_note_as_the_reason(migrated: Engine) -> None:
    conversation_id = new_chat(migrated)
    _, job = add_running_event(migrated, conversation_id, EventKind.SCHEDULED_MESSAGE, "Augurargli buon compleanno")
    runtime = FakeRuntime([], ["Buon compleanno Utente!"])

    result = await run(service(migrated, runtime), job)

    assert result["status"] == "delivered"
    assert runtime.decision_requests == []
    assert "Motivo: Augurargli buon compleanno" in runtime.requests[0].prompt


@pytest.mark.anyio
async def test_a_text_similar_to_a_recent_proactive_message_is_discarded_and_generated_once_more(
    migrated: Engine,
) -> None:
    """T-SCH-05."""
    conversation_id = new_chat(migrated)
    add_proactive_message(migrated, conversation_id, EARLIER)
    event_id, job = add_running_event(migrated, conversation_id)
    similar = "Ciao Utente, oggi ho pensato a te e al tuo colloquio. Com'è andato?"
    runtime = FakeRuntime([decision(True)], [similar, FRESH])

    result = await run(service(migrated, runtime), job)

    assert result["status"] == "delivered"
    assert len(runtime.requests) == 2
    assert [content for content, _, _ in proactive_messages(migrated)] == [EARLIER, FRESH]
    assert event_row(migrated, event_id)[0] == "delivered"
    query = text("SELECT checks_json FROM message_variants WHERE message_id = :id")
    checks = read(migrated, lambda c: json.loads(c.execute(query, {"id": result["message_id"]}).scalar_one()))
    assert checks["restarts"] == ["repetition"]


@pytest.mark.anyio
async def test_a_second_similar_text_ends_the_event_skipped_and_writes_nothing(migrated: Engine) -> None:
    """T-SCH-05."""
    conversation_id = new_chat(migrated)
    add_proactive_message(migrated, conversation_id, EARLIER)
    event_id, job = add_running_event(migrated, conversation_id)
    runtime = FakeRuntime([decision(True)], [EARLIER, EARLIER])

    result = await run(service(migrated, runtime), job)

    assert result == {"status": "skipped", "reason": "repetition"}
    assert len(runtime.requests) == 2
    assert event_row(migrated, event_id) == ("skipped", "repetition", None)
    assert [content for content, _, _ in proactive_messages(migrated)] == [EARLIER]


@pytest.mark.anyio
async def test_a_conversation_switched_off_since_the_job_was_queued_gets_nothing(migrated: Engine) -> None:
    conversation_id = new_chat(migrated)
    event_id, job = add_running_event(migrated, conversation_id)
    set_proactive(migrated, conversation_id, json.dumps({"enabled": False}))
    runtime = FakeRuntime([], [])

    result = await run(service(migrated, runtime), job)

    assert result == {"status": "skipped", "reason": "switched_off"}
    assert event_row(migrated, event_id) == ("skipped", "switched_off", None)
    assert runtime.decision_requests == []
    assert runtime.requests == []


@pytest.mark.anyio
async def test_a_refused_decision_fails_the_event_only_on_the_last_attempt(migrated: Engine) -> None:
    conversation_id = new_chat(migrated)
    event_id, job = add_running_event(migrated, conversation_id)
    handler = service(migrated, FakeRuntime(["non json", "ancora no", "non json", "ancora no"], []))

    with pytest.raises(JobFailedError) as first:
        await handler(dataclasses.replace(job, attempt=1), RunContext(lease=None, genkey="g"))
    assert first.value.retryable
    assert event_row(migrated, event_id)[0] == "running"

    with pytest.raises(JobFailedError):
        await handler(dataclasses.replace(job, attempt=job.max_attempts), RunContext(lease=None, genkey="g"))
    assert event_row(migrated, event_id) == ("failed", "STRUCTURED_OUTPUT_INVALID", None)
    assert proactive_messages(migrated) == []


@pytest.mark.anyio
async def test_a_cancelled_job_ends_its_event_cancelled(migrated: Engine) -> None:
    conversation_id = new_chat(migrated)
    event_id, job = add_running_event(migrated, conversation_id)

    await service(migrated, FakeRuntime([], [])).cancelled(job)

    assert event_row(migrated, event_id)[0] == "cancelled"
