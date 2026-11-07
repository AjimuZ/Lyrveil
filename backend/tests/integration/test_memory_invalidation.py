"""T-MEM-18, T-MEM-19, T-MEM-20 and T-CON-04: invalidation after message changes, re-evaluation, a restart and the
canonical data, on a real database.

Messages change through MessageService, which invalidates in the transaction of the change; the extraction,
`memory_reevaluation` and `summary_checkpoint` jobs run by calling their handlers, or through the real JobScheduler,
with a scripted in-process text runtime. Checks that an edit or a deletion weakens or invalidates the memories,
episodes, state rows and summaries that rested on the old text and queues their rebuilding; that re-evaluation brings
back what still holds without counting a relationship change twice, and a deleted change gives the earlier fact back;
that a locked memory is only flagged; that a restart keeps memories and cursors and resumes the interrupted run; and
that a long chat with extraction and summaries never changes the character's card.
"""

import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, RowMapping, text

from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import Author, MessageStatus, VariantSource, VariantStatus, content_hash
from lyrveil.jobs.handlers import JobDone, JobHandler, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, recover_interrupted_jobs, write
from lyrveil.memory.window import ExtractionSettings
from lyrveil.orchestration.client import Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence import messages as message_repository
from lyrveil.persistence import summaries as summary_repository
from lyrveil.persistence.database import create_database_engine
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.services.conversations import ConversationService
from lyrveil.services.memory_extraction import EXTRACTION_GRAMMAR, MemoryExtractionService, queue_extraction_if_due
from lyrveil.services.messages import MessageService
from lyrveil.services.summaries import SummaryService

START = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
CONTEXT = RunContext(lease=None, genkey="genkey")
CHAT = (
    (Author.USER, "Ciao! Oggi ho iniziato a lavorare allo Studio Rossi."),
    (Author.CHARACTER, "Che bello! Com'è l'ufficio?"),
    (Author.USER, "Piccolo ma luminoso. A casa mi aspetta il mio gatto Briciola."),
    (Author.CHARACTER, "Briciola è un nome perfetto per un gatto."),
    (Author.USER, "Questa settimana sono stanchissima, dormo poco."),
    (Author.CHARACTER, "Allora stasera riposati."),
    (Author.USER, "Ci provo. Domani esco con Marta."),
    (Author.CHARACTER, "Salutamela!"),
    (Author.USER, "Sarà fatto."),
    (Author.CHARACTER, "A domani, allora."),
)
# The tables of the character's canonical definition (docs/memory-engine.md, Memory layers, layer A).
CANONICAL_TABLES = ("characters", "character_traits", "character_visual_profiles", "character_versions")


class Clock:
    """A clock the test moves by hand, shared by the queue and the services."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class Crash(BaseException):
    """Stands for the process dying in the middle of a run: no handler catches it, so nothing after it runs."""


class ScriptedRuntime:
    """A TextRuntime whose `generate` answers the scripted texts in order, or `answer(request)` when given, and keeps
    every request. With `crash` set, `generate` raises Crash instead."""

    def __init__(self, *outputs: str, answer: Callable[[TextRequest], str] | None = None) -> None:
        self.outputs = list(outputs)
        self.answer = answer
        self.crash = False
        self.requests: list[TextRequest] = []

    def script(self, *outputs: str) -> None:
        self.outputs.extend(outputs)

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        yield TextChunk("", "stop", "")

    async def generate(self, request: TextRequest) -> str:
        self.requests.append(request)
        if self.crash:
            raise Crash
        if self.answer is not None:
            return self.answer(request)
        return self.outputs.pop(0)

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


class Leases:
    """Grants every lease, as an orchestrator in LLM_READY with no other job would."""

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        return Lease(id=f"lease-{job_id}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)

    def renew_lease(self, lease: Lease) -> Lease:
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        return True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


@pytest.fixture
def clock() -> Clock:
    return Clock()


def rows(engine: Engine, sql: str, **params: object) -> list[RowMapping]:
    return read(engine, lambda connection: list(connection.execute(text(sql), params).mappings().all()))


def count(engine: Engine, table: str) -> int:
    return int(rows(engine, f"SELECT count(*) AS n FROM {table}")[0]["n"])


def values(engine: Engine, sql: str, **params: object) -> list[tuple[Any, ...]]:
    """Returns the rows of `sql` as tuples, in the order of its columns."""
    return [tuple(dict(row).values()) for row in rows(engine, sql, **params)]


def revisions_of(engine: Engine, memory_id: str) -> list[tuple[Any, ...]]:
    """Returns `(version, actor, reason)` of each revision of the memory, oldest first."""
    sql = "SELECT version, actor, reason FROM memory_revisions WHERE memory_id = :id ORDER BY version"
    return values(engine, sql, id=memory_id)


def start_chat(engine: Engine) -> str:
    character_id = new_id()
    now = to_utc_text(START)
    write(
        engine,
        lambda connection: connection.execute(
            text(
                "INSERT INTO characters (id, first_name, last_name, age, status, created_at, updated_at) "
                "VALUES (:id, 'Dario', 'Venturi', 34, 'active', :now, :now)"
            ),
            {"id": character_id, "now": now},
        ),
    )
    return ConversationService(engine).create(character_id).id


def add_messages(engine: Engine, conversation_id: str, turns: Sequence[tuple[Author, str]]) -> list[str]:
    """Stores complete messages, each with its one active variant, in one transaction, and returns their ids."""
    now = to_utc_text(START)

    def work(connection: Connection) -> list[str]:
        ids: list[str] = []
        for author, content in turns:
            message_id, variant_id = new_id(), new_id()
            message_repository.insert_message(
                connection,
                message_id=message_id,
                conversation_id=conversation_id,
                seq=message_repository.take_seq(connection, conversation_id, now),
                client_message_id=None,
                author=author,
                status=MessageStatus.COMPLETE,
                read_state="sent" if author is Author.USER else None,
                content=content,
                now=now,
            )
            message_repository.insert_variant(
                connection,
                variant_id=variant_id,
                message_id=message_id,
                content=content,
                content_hash=content_hash(content),
                source=VariantSource.USER if author is Author.USER else VariantSource.GENERATED,
                status=VariantStatus.COMPLETE,
                now=now,
            )
            message_repository.update_message(connection, message_id, active_variant_id=variant_id)
            ids.append(message_id)
        return ids

    return write(engine, work)


def memory(**fields: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "relation": "new",
        "target_ref": None,
        "kind": "user_fact",
        "content": "Lavora allo Studio Rossi.",
        "subject": "user.employer",
        "value": "Studio Rossi",
        "provenance": "stated",
        "durability": "lasting",
        "valid_until": None,
        "importance": 4,
        "confidence": "high",
        "source_seqs": [1],
    }
    item.update(fields)
    return item


def episode(**fields: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": "Primo giorno allo Studio Rossi",
        "description": "Racconta il suo primo giorno di lavoro allo Studio Rossi.",
        "participants": ["Utente", "Dario"],
        "importance": 3,
        "source_seqs": [1, 2],
        "linked_episode_refs": [],
    }
    item.update(fields)
    return item


def state(**fields: Any) -> dict[str, Any]:
    update: dict[str, Any] = {
        "emotional_state": None,
        "current_topic": None,
        "open_threads_add": [],
        "open_threads_close": [],
        "goals": [],
        "narrative_change": None,
    }
    update.update(fields)
    return update


def output(memories: list[dict[str, Any]], **parts: Any) -> str:
    result: dict[str, Any] = {
        "schema_version": 1,
        "memories": memories,
        "episodes": [],
        "relationship_events": [],
        "state": state(),
    }
    result.update(parts)
    return json.dumps(result, ensure_ascii=False)


def trust_up(seq: int) -> dict[str, Any]:
    return {
        "dimension": "trust",
        "direction": "up",
        "magnitude": "small",
        "reason": "Si confida.",
        "source_seqs": [seq],
    }


SUMMARY = json.dumps(
    {
        "schema_version": 1,
        "text": "Il primo giorno allo Studio Rossi e il gatto Briciola.",
        "events": ["Il primo giorno di lavoro"],
        "decisions": [],
        "relationship_changes": [],
        "unresolved": [],
        "key_facts": ["Ha un gatto"],
        "source_seqs": [1, 3],
    },
    ensure_ascii=False,
)


def queued(engine: Engine, queue: JobQueue, kind: JobKind) -> list[Job]:
    """Returns the queued jobs of `kind`, oldest first."""

    def load(connection: Connection) -> list[Job]:
        ids = connection.execute(
            text("SELECT id FROM generation_jobs WHERE kind = :kind AND status = 'queued' ORDER BY created_at, id"),
            {"kind": kind.value},
        ).scalars()
        found = [queue.get(connection, job_id) for job_id in list(ids)]
        return [job for job in found if job is not None]

    return read(engine, load)


async def run(handler: JobHandler, job: Job) -> dict[str, Any]:
    """Runs `job` once with `handler`, outside the scheduler, and returns its result."""
    done = await handler(job, CONTEXT)
    assert isinstance(done, JobDone)
    assert done.result is not None
    return dict(done.result)


def insert_checkpoint(engine: Engine, conversation_id: str, level: int, first: int, last: int) -> None:
    """Stores an active version-1 checkpoint of the range, as a finished summary run would."""
    checkpoint = summary_repository.NewCheckpoint(
        id=new_id(),
        conversation_id=conversation_id,
        level=level,
        from_seq=first,
        to_seq=last,
        version=1,
        content={"events": [], "decisions": [], "relationship_changes": [], "unresolved": [], "key_facts": []},
        text=f"Riassunto da {first} a {last}.",
        source_hash="hash",
        prompt_version="summary.v1",
        token_count=5,
    )
    write(engine, lambda connection: summary_repository.insert_checkpoint(connection, checkpoint, to_utc_text(START)))


class Chat:
    """A seeded chat with its extraction job and service, and the MessageService that changes its messages."""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self.engine = engine
        self.conversation_id = start_chat(engine)
        self.message_ids = add_messages(engine, self.conversation_id, CHAT)
        self.queue = JobQueue(clock=clock)
        self.runtime = ScriptedRuntime()
        self.service = MemoryExtractionService(engine, self.runtime, queue=self.queue, clock=clock)
        self.messages = MessageService(engine, self.queue, clock=clock)
        job = write(
            engine,
            lambda connection: queue_extraction_if_due(
                connection, self.queue, self.conversation_id, ExtractionSettings()
            ),
        )
        assert job is not None, "ten unprocessed messages make the chat due"
        self.job: Job = job

    async def extract(self, answer: str, *turns: tuple[Author, str]) -> dict[str, Any]:
        """Adds `turns` as new messages, runs the next window with `answer` and returns the job's result."""
        self.message_ids += add_messages(self.engine, self.conversation_id, turns)
        self.runtime.script(answer)
        result = await run(self.service, self.job)
        assert result["status"] == "completed"
        return result

    async def reevaluate(self, answer: str) -> dict[str, Any]:
        """Runs the one queued re-evaluation with `answer` and returns its result."""
        [job] = queued(self.engine, self.queue, JobKind.MEMORY_REEVALUATION)
        self.runtime.script(answer)
        return await run(self.service, job)

    def memories(self) -> dict[str, RowMapping]:
        found = rows(self.engine, "SELECT * FROM memories WHERE conversation_id = :id", id=self.conversation_id)
        return {row["content"]: row for row in found}

    def variant(self, number: int) -> str:
        """Returns the active variant of message `number` (its seq)."""
        sql = "SELECT active_variant_id FROM messages WHERE id = :id"
        [row] = rows(self.engine, sql, id=self.message_ids[number - 1])
        return str(row["active_variant_id"])

    def sources(self, memory_id: str) -> set[tuple[str, str]]:
        sql = "SELECT message_id, variant_id FROM memory_sources WHERE memory_id = :id"
        return {(row["message_id"], row["variant_id"]) for row in rows(self.engine, sql, id=memory_id)}

    def shown(self) -> list[str]:
        """Returns the existing-memory lines of the last prompt."""
        section = self.runtime.requests[-1].prompt.rsplit("Ricordi esistenti:\n", 1)[1]
        return section.split("\nEpisodi recenti:", 1)[0].splitlines()


EDITED = "Piccolo ma luminoso. A casa mi aspetta il mio gatto Briciola, che dorme sempre."
OFFICE = "Il suo ufficio è piccolo e luminoso."
CAT = memory(content="Ha un gatto di nome Briciola.", subject="user.pet", value="Briciola", source_seqs=[3, 4])
CAT_EPISODE = episode(
    title="Il gatto Briciola", description="Racconta del gatto Briciola che la aspetta a casa.", source_seqs=[3]
)


@pytest.mark.anyio
async def test_an_edit_invalidates_derived_data_and_reevaluation_restores_what_holds(
    migrated: Engine, clock: Clock
) -> None:
    """T-MEM-19, edit: the memory resting only on the old text is invalidated, one with another source loses it and
    0.1 of confidence, the episode and state row citing it are invalidated, and the summaries covering it are
    invalidated and queued again. Re-evaluation brings the same fact and episode back on their rows, and does not count
    the relationship change a remaining row already holds."""
    chat = Chat(migrated, clock)
    await chat.extract(
        output(
            [
                memory(),
                CAT,
                memory(content=OFFICE, subject=None, value=None, importance=2, source_seqs=[3]),
                memory(content="Domani esce con Marta.", subject=None, value=None, importance=2, source_seqs=[7]),
            ],
            episodes=[episode(), CAT_EPISODE],
            relationship_events=[trust_up(5)],
            state=state(emotional_state="Stanca ma serena"),
        )
    )
    for level, first, last in ((0, 1, 5), (0, 6, 10), (1, 1, 10)):
        insert_checkpoint(migrated, chat.conversation_id, level, first, last)
    [before] = rows(migrated, "SELECT trust, emotional_state FROM relationship_states")
    old_variant = chat.variant(3)
    clock.now = START + timedelta(minutes=5)

    chat.messages.edit(chat.message_ids[2], EDITED)

    stored = chat.memories()
    employer, cat, office = stored["Lavora allo Studio Rossi."], stored[CAT["content"]], stored[OFFICE]
    marta = stored["Domani esce con Marta."]
    assert (employer["status"], employer["version"], employer["confidence"]) == ("active", 1, 0.95)
    assert (cat["status"], cat["version"], cat["confidence"]) == ("active", 2, 0.85)
    assert chat.sources(cat["id"]) == {(chat.message_ids[3], chat.variant(4))}, "the stale source is dropped"
    assert (office["status"], office["version"]) == ("invalidated", 2)
    assert chat.sources(office["id"]) == {(chat.message_ids[2], old_variant)}, "it keeps what it rested on"
    assert (marta["status"], marta["version"]) == ("active", 1)
    assert set(values(migrated, "SELECT memory_id, version, actor, reason, run_id FROM memory_revisions")) == {
        (cat["id"], 1, "consistency", "source_changed", None),
        (office["id"], 1, "consistency", "invalidated", None),
    }
    episodes = {row["title"]: row for row in rows(migrated, "SELECT id, title, status FROM episodic_memories")}
    assert {title: row["status"] for title, row in episodes.items()} == {
        "Primo giorno allo Studio Rossi": "active",
        "Il gatto Briciola": "invalidated",
    }
    history = rows(migrated, "SELECT event_kind, status FROM character_state_history")
    assert {(row["event_kind"], row["status"]) for row in history} == {
        ("relationship_change", "applied"),
        ("emotion_change", "invalidated"),
    }, "the emotion row cites the whole window; the trust row cites message 5 only"
    [after] = rows(migrated, "SELECT trust, emotional_state, recent_event_ids_json FROM relationship_states")
    assert (after["trust"], after["emotional_state"]) == (before["trust"], None)
    assert json.loads(after["recent_event_ids_json"]) == [episodes["Primo giorno allo Studio Rossi"]["id"]]
    checkpoints = rows(migrated, "SELECT level, from_seq, to_seq, version, status FROM conversation_summaries")
    assert {(row["level"], row["from_seq"], row["to_seq"], row["version"]): row["status"] for row in checkpoints} == {
        (0, 1, 5, 1): "invalidated",
        (0, 6, 10, 1): "active",
        (1, 1, 10, 1): "invalidated",
    }
    jobs = rows(
        migrated,
        "SELECT kind, payload_json FROM generation_jobs WHERE kind <> 'memory_extraction' AND status = 'queued' "
        "ORDER BY kind",
    )
    assert [(row["kind"], json.loads(row["payload_json"])) for row in jobs] == [
        ("memory_reevaluation", {"from_seq": 1, "to_seq": 9}),
        ("summary_checkpoint", {"from_seq": 1, "to_seq": 5}),
    ]

    result = await chat.reevaluate(
        output(
            [memory(content=OFFICE, subject=None, value=None, importance=2, source_seqs=[3])],
            episodes=[CAT_EPISODE],
            relationship_events=[trust_up(5)],
            state=state(emotional_state="Stanca ma serena"),
        )
    )

    prompt = chat.runtime.requests[-1].prompt
    assert f"[3] Utente: {EDITED}" in prompt
    assert "[9] Utente: Sarà fatto." in prompt
    assert "A domani, allora." not in prompt, "the range ends six messages after the change"
    assert all("ufficio" not in line for line in chat.shown()), "an invalidated memory is never shown"
    assert (result["status"], result["from_seq"], result["to_seq"]) == ("completed", 1, 9)
    assert (result["memories_added"], result["memories_reactivated"], result["episodes_reactivated"]) == (0, 1, 1)
    assert (result["state_events"], result["state_skipped"], result["state_update_skipped"]) == (1, 1, False)
    office = chat.memories()[OFFICE]
    assert (office["status"], office["version"], office["confidence"]) == ("active", 3, 0.95)
    assert chat.sources(office["id"]) == {(chat.message_ids[2], chat.variant(3))}, "the new text is its source now"
    assert revisions_of(migrated, office["id"]) == [
        (1, "consistency", "invalidated"),
        (2, "extraction", "reevaluation"),
    ]
    assert count(migrated, "memories") == 4, "the same fact comes back on its row"
    assert {row["status"] for row in rows(migrated, "SELECT status FROM episodic_memories")} == {"active"}
    [restored] = rows(migrated, "SELECT trust, emotional_state FROM relationship_states")
    assert (restored["trust"], restored["emotional_state"]) == (before["trust"], "Stanca ma serena")
    [cursor] = rows(migrated, "SELECT last_extracted_seq FROM conversations")
    assert cursor["last_extracted_seq"] == 10, "a re-evaluation never moves the cursor"

    [summary_job] = queued(migrated, chat.queue, JobKind.SUMMARY_CHECKPOINT)
    chat.runtime.script(SUMMARY)
    summarized = await run(SummaryService(migrated, chat.runtime, queue=chat.queue, clock=clock), summary_job)

    assert (summarized["status"], summarized["kind"], summarized["version"]) == ("completed", "resummarize", 2)
    active = "SELECT level, from_seq, to_seq, version FROM conversation_summaries WHERE status = 'active'"
    assert set(values(migrated, active)) == {(0, 1, 5, 2), (0, 6, 10, 1)}


@pytest.mark.anyio
async def test_deleting_a_change_gives_the_earlier_fact_back(migrated: Engine, clock: Clock) -> None:
    """T-MEM-19, deletion: the memory a deleted message stated is invalidated, the fact it had superseded is active
    again with a revision, and a re-evaluation that finds nothing new leaves it so."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory()]))
    update = memory(
        relation="update", target_ref="E1", content="Lavora da Verdi & Co.", value="Verdi & Co", source_seqs=[11]
    )
    await chat.extract(
        output([update]),
        (Author.USER, "Ho cambiato lavoro, ora sono da Verdi & Co."),
        (Author.CHARACTER, "Congratulazioni!"),
    )
    assert chat.memories()["Lavora allo Studio Rossi."]["status"] == "superseded"
    clock.now = START + timedelta(minutes=5)

    chat.messages.delete(chat.message_ids[10])

    stored = chat.memories()
    rossi, verdi = stored["Lavora allo Studio Rossi."], stored["Lavora da Verdi & Co."]
    assert (verdi["status"], verdi["version"]) == ("invalidated", 2)
    assert (rossi["status"], rossi["version"]) == ("active", 3)
    assert (rossi["valid_to"], rossi["superseded_by_id"]) == (None, None)
    assert revisions_of(migrated, rossi["id"]) == [
        (1, "extraction", "update"),
        (2, "consistency", "replacement_invalidated"),
    ]

    result = await chat.reevaluate(output([]))

    assert (result["status"], result["from_seq"], result["to_seq"]) == ("completed", 5, 12)
    data = chat.runtime.requests[-1].prompt.rsplit("Ricordi esistenti:\n", 1)[1]
    assert "Verdi" not in data, "the deleted message and the memory it stated are both left out"
    assert "[12] Dario: Congratulazioni!" in data
    assert chat.shown() == ["E1: Lavora allo Studio Rossi."]
    stored = chat.memories()
    assert stored["Lavora allo Studio Rossi."]["status"] == "active"
    assert stored["Lavora da Verdi & Co."]["status"] == "invalidated"


@pytest.mark.anyio
async def test_a_locked_memory_gets_needs_review_and_does_not_change(migrated: Engine, clock: Clock) -> None:
    """T-MEM-20: editing the source of a locked memory sets `needs_review` and nothing else, with no revision; the
    re-evaluation that reads the new text leaves it alone too and stores the new information uncertain for review."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory()]))
    write(migrated, lambda connection: connection.execute(text("UPDATE memories SET locked = 1")))
    before = dict(chat.memories()["Lavora allo Studio Rossi."])
    sources = chat.sources(before["id"])
    clock.now = START + timedelta(minutes=5)

    chat.messages.edit(chat.message_ids[0], "Ciao! Oggi ho iniziato a lavorare allo Studio Bianchi.")

    assert dict(chat.memories()["Lavora allo Studio Rossi."]) == {**before, "needs_review": 1}
    assert chat.sources(before["id"]) == sources
    assert count(migrated, "memory_revisions") == 0

    bianchi = memory(relation="update", target_ref="E1", content="Lavora allo Studio Bianchi.", value="Studio Bianchi")
    result = await chat.reevaluate(output([bianchi]))

    assert (result["status"], result["memories_added"], result["memories_superseded"]) == ("completed", 1, 0)
    stored = chat.memories()
    assert dict(stored["Lavora allo Studio Rossi."]) == {**before, "needs_review": 1}
    new = stored["Lavora allo Studio Bianchi."]
    assert (new["status"], new["needs_review"]) == ("uncertain", 1)
    assert count(migrated, "memory_revisions") == 0


# The tables a run writes, which a run cut by a crash must leave as they were.
DERIVED = ("memories", "memory_sources", "memory_fts", "episodic_memories", "episodic_sources", "relationship_states")


def derived_rows(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    """Returns every row of the memory tables, in storage order."""
    return {table: [dict(row) for row in rows(engine, f"SELECT * FROM {table} ORDER BY rowid")] for table in DERIVED}


@pytest.mark.anyio
async def test_a_restart_keeps_memories_and_cursors_and_resumes_the_interrupted_run(
    migrated: Engine, database_path: Path, clock: Clock
) -> None:
    """T-MEM-18: a run cut by the process dying leaves the memories and cursor of the run before it; after a restart
    the job is queued again, the same run resumes with its next attempt and completes once."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory()], episodes=[episode()]))
    turns = [(Author.USER if n % 2 else Author.CHARACTER, f"Messaggio {n}: parliamo del mare.") for n in range(11, 21)]
    add_messages(migrated, chat.conversation_id, turns)
    write(migrated, lambda connection: chat.queue.start(connection, chat.job.id, None, "genkey-1"))
    chat.runtime.crash = True
    kept = derived_rows(migrated)

    with pytest.raises(Crash):
        await chat.service(chat.job, CONTEXT)
    migrated.dispose()

    restarted = create_database_engine(database_path)
    try:
        upgrade_to_head(restarted)
        queue = JobQueue(clock=clock)
        [recovered] = recover_interrupted_jobs(restarted, queue)
        assert (recovered.id, recovered.status) == (chat.job.id, JobStatus.QUEUED)
        assert recovered.error_code == "JOB_INTERRUPTED"
        assert derived_rows(restarted) == kept
        [cursors] = rows(restarted, "SELECT last_extracted_seq, last_summarized_seq FROM conversations")
        assert (cursors["last_extracted_seq"], cursors["last_summarized_seq"]) == (10, 0)
        runs = "SELECT from_seq, to_seq, status, attempt FROM memory_extraction_runs ORDER BY from_seq"
        assert values(restarted, runs) == [(1, 10, "completed", 1), (11, 20, "running", 1)]

        runtime = ScriptedRuntime(output([memory(content="Ama il mare.", subject=None, value=None, source_seqs=[11])]))
        service = MemoryExtractionService(restarted, runtime, queue=queue, clock=clock)
        handlers: dict[JobKind, JobHandler] = {JobKind.MEMORY_EXTRACTION: service, JobKind.MEMORY_REEVALUATION: service}
        finished = await JobScheduler(restarted, queue, handlers, Leases(), lambda: RequiredMode.LLM).run_once()

        assert finished is not None
        assert (finished.id, finished.status, finished.attempt) == (chat.job.id, JobStatus.COMPLETED, 2)
        assert finished.result is not None
        assert (finished.result["run_attempt"], finished.result["from_seq"], finished.result["to_seq"]) == (2, 11, 20)
        assert values(restarted, runs) == [(1, 10, "completed", 1), (11, 20, "completed", 2)]
        assert sorted(row["content"] for row in rows(restarted, "SELECT content FROM memories")) == [
            "Ama il mare.",
            "Lavora allo Studio Rossi.",
        ]
        [cursors] = rows(restarted, "SELECT last_extracted_seq FROM conversations")
        assert cursors["last_extracted_seq"] == 20
    finally:
        restarted.dispose()


def canonical_hash(engine: Engine) -> str:
    """Returns the SHA-256 of every row of the canonical tables, in storage order."""
    digest = hashlib.sha256()
    for table in CANONICAL_TABLES:
        for row in rows(engine, f"SELECT * FROM {table} ORDER BY rowid"):
            digest.update(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode())
    return digest.hexdigest()


def synthetic_answer(request: TextRequest) -> str:
    """Answers an extraction with a fact, an identity candidate about the character, a relationship event and a mood,
    all on the newest message of the window; answers a summary with SUMMARY."""
    if request.grammar != EXTRACTION_GRAMMAR:
        return SUMMARY
    window = request.prompt.rsplit("Messaggi da analizzare:\n", 1)[1]
    seq = max(int(found) for found in re.findall(r"^\[(\d+)\] ", window, flags=re.MULTILINE))
    return output(
        [
            memory(content=f"Ricorda il viaggio numero {seq}.", subject=None, value=None, source_seqs=[seq]),
            memory(
                kind="identity_candidate",
                content=f"Dario dice di avere {seq} anni.",
                subject="character.age",
                value=str(seq),
                provenance="fictional",
                source_seqs=[seq],
            ),
        ],
        relationship_events=[trust_up(seq)],
        state=state(emotional_state=f"Curioso del viaggio {seq % 3}"),
    )


@pytest.mark.anyio
async def test_a_long_chat_never_changes_the_canonical_data(migrated: Engine, clock: Clock) -> None:
    """T-CON-04: 1000 synthetic messages, every extraction window and every summary leave the hash of the character's
    canonical rows unchanged, although each window proposes a new age for the character."""
    conversation_id = start_chat(migrated)
    [character] = rows(migrated, "SELECT id FROM characters")
    now = to_utc_text(START)
    write(
        migrated,
        lambda connection: connection.execute(
            text(
                "INSERT INTO character_traits (id, character_id, category, description, sort_order, created_at, "
                "updated_at) VALUES (:id, :character_id, 'main_trait', 'Curioso e paziente.', 0, :now, :now)"
            ),
            {"id": new_id(), "character_id": character["id"], "now": now},
        ),
    )
    write(
        migrated,
        lambda connection: connection.execute(
            text(
                "INSERT INTO character_visual_profiles (character_id, hair, updated_at) VALUES (:id, 'castani', :now)"
            ),
            {"id": character["id"], "now": now},
        ),
    )
    before = canonical_hash(migrated)
    turns = [
        (Author.USER if n % 2 else Author.CHARACTER, f"Messaggio {n}: oggi ti racconto del viaggio numero {n}.")
        for n in range(1, 1001)
    ]
    add_messages(migrated, conversation_id, turns)
    queue = JobQueue(clock=clock)
    runtime = ScriptedRuntime(answer=synthetic_answer)
    extraction = write(
        migrated, lambda connection: queue_extraction_if_due(connection, queue, conversation_id, ExtractionSettings())
    )
    assert extraction is not None
    summary = write(
        migrated,
        lambda connection: queue.enqueue(
            connection, JobKind.SUMMARY_CHECKPOINT, {}, conversation_id=conversation_id, character_id=character["id"]
        ),
    )

    windows = 0
    extractor = MemoryExtractionService(migrated, runtime, queue=queue, clock=clock)
    while (await run(extractor, extraction))["status"] == "completed":
        windows += 1
    steps = 0
    summarizer = SummaryService(migrated, runtime, queue=queue, clock=clock)
    while (await run(summarizer, summary))["status"] == "completed":
        steps += 1

    assert canonical_hash(migrated) == before
    assert count(migrated, "character_versions") == 0
    assert windows == 84, "1000 messages in windows of 12"
    [cursors] = rows(migrated, "SELECT last_extracted_seq, last_summarized_seq FROM conversations")
    assert (cursors["last_extracted_seq"], cursors["last_summarized_seq"]) == (1000, 960)
    candidates = rows(migrated, "SELECT count(*) AS n FROM memories WHERE kind = 'identity_candidate'")
    assert candidates[0]["n"] == windows, "each proposed age is kept as a candidate only"
    assert steps == count(migrated, "conversation_summaries") == 24 + 5, "24 chunks of 40, then 5 rollups"
