"""T-MEM-01, T-MEM-02, T-MEM-03, T-MEM-10 to T-MEM-13, T-CON-05 and the scheduler half of T-MEM-17: memory extraction
on a real database.

The extraction job runs through the real JobScheduler under a granted llm lease, or by calling the handler directly,
with MemoryExtractionService and a scripted in-process text runtime. Checks that valid output stores its items with
their sources, index rows, run and cursor; that invalid output stores nothing, keeps the cursor and is retried after
the backoff; that the same window twice gives one result; that the job waits in image mode; that the trigger queues one
job when a chat is due; and that the consistency rules hold over consecutive windows: an update supersedes with
history, a contradiction stays uncertain and labeled, a temporary memory expires beside the lasting one, a user
correction wins, and an identity candidate changes nothing.
"""

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, RowMapping, text

from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import Author, MessageStatus, VariantSource, VariantStatus, content_hash
from lyrveil.events.hub import EventHub
from lyrveil.jobs.handlers import JobDone, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.memory.window import ExtractionSettings
from lyrveil.orchestration.client import Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence import messages as message_repository
from lyrveil.persistence.memory_index import match_memory_ids
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.services.chat_inference import ChatInferenceService
from lyrveil.services.conversations import ConversationService
from lyrveil.services.memory_extraction import EXTRACTION_GRAMMAR, MemoryExtractionService, queue_extraction_if_due
from lyrveil.services.messages import MessageService

START = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
NOT_JSON = "non è JSON"
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


class Clock:
    """A clock the test moves by hand, shared by the queue and the service."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class ScriptedRuntime:
    """A TextRuntime whose `generate` answers the scripted texts in order and keeps every request, and whose `stream`
    answers a fixed reply."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.requests: list[TextRequest] = []

    def script(self, *outputs: str) -> None:
        self.outputs.extend(outputs)

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        yield TextChunk("Va bene")
        yield TextChunk("", "stop", "Va bene")

    async def generate(self, request: TextRequest) -> str:
        self.requests.append(request)
        return self.outputs.pop(0)

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


class Leases:
    """Grants every lease and records each one, as an orchestrator in LLM_READY with no other job would."""

    def __init__(self) -> None:
        self.acquired: list[tuple[LeaseMode, str]] = []

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        self.acquired.append((mode, job_id))
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


def add_message(engine: Engine, conversation_id: str, author: Author, content: str) -> str:
    """Stores a complete message with its one active variant and returns its id."""
    now = to_utc_text(START)

    def work(connection: Connection) -> str:
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
        return message_id

    return write(engine, work)


def seeded_chat(engine: Engine) -> tuple[str, list[str]]:
    conversation_id = start_chat(engine)
    return conversation_id, [add_message(engine, conversation_id, author, line) for author, line in CHAT]


def enqueue(engine: Engine, queue: JobQueue, conversation_id: str) -> Job:
    def work(connection: Connection) -> Job:
        job = queue_extraction_if_due(connection, queue, conversation_id, ExtractionSettings())
        assert job is not None, "ten unprocessed messages make the chat due"
        return job

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


def output(memories: list[dict[str, Any]], **parts: Any) -> str:
    result: dict[str, Any] = {
        "schema_version": 1,
        "memories": memories,
        "episodes": [],
        "relationship_events": [],
        "state": {
            "emotional_state": None,
            "current_topic": None,
            "open_threads_add": [],
            "open_threads_close": [],
            "goals": [],
            "narrative_change": None,
        },
    }
    result.update(parts)
    return json.dumps(result, ensure_ascii=False)


VALID = output(
    [
        memory(),
        memory(
            content="Ha un gatto di nome Briciola.",
            subject="user.pet",
            value="Briciola",
            provenance="inferred",
            source_seqs=[3, 4, 3],
        ),
        memory(
            relation="temporary",
            kind="user_fact",
            content="Questa settimana è molto stanca.",
            subject=None,
            value=None,
            durability="temporary",
            importance=2,
            confidence="medium",
            source_seqs=[5],
        ),
        memory(content="Ha cinque fratelli.", source_seqs=[99]),
        memory(relation="duplicate", target_ref="E7", content="Lavora a Milano."),
        memory(relation="update", target_ref=None, content="Lavora da Verdi & Co."),
    ],
    episodes=[
        {
            "title": "Primo giorno allo Studio Rossi",
            "description": "Racconta il suo primo giorno di lavoro allo Studio Rossi.",
            "participants": ["Utente", "Dario"],
            "importance": 3,
            "source_seqs": [1, 2],
            "linked_episode_refs": [],
        }
    ],
    relationship_events=[
        {"dimension": "trust", "direction": "up", "magnitude": "small", "reason": "Si confida.", "source_seqs": [5]},
        {"dimension": "trust", "direction": "up", "magnitude": "small", "reason": "Fuori.", "source_seqs": [50]},
    ],
)


def in_mode(mode: RequiredMode) -> Callable[[], RequiredMode]:
    """Returns a `current_mode` for JobScheduler that always reports `mode`."""
    return lambda: mode


def scheduler(engine: Engine, runtime: ScriptedRuntime, queue: JobQueue, clock: Clock, leases: Leases) -> JobScheduler:
    service = MemoryExtractionService(engine, runtime, queue=queue, clock=clock)
    return JobScheduler(engine, queue, {JobKind.MEMORY_EXTRACTION: service}, leases, lambda: RequiredMode.LLM)


def rows(engine: Engine, sql: str, **params: object) -> list[RowMapping]:
    return read(engine, lambda connection: list(connection.execute(text(sql), params).mappings().all()))


def count(engine: Engine, table: str) -> int:
    return int(rows(engine, f"SELECT count(*) AS n FROM {table}")[0]["n"])


def cursor(engine: Engine, conversation_id: str) -> int:
    found = rows(engine, "SELECT last_extracted_seq FROM conversations WHERE id = :id", id=conversation_id)
    return int(found[0]["last_extracted_seq"])


def stored_state(engine: Engine) -> dict[str, int]:
    tables = ("memories", "memory_sources", "memory_fts", "episodic_memories", "episodic_sources", "episode_fts")
    return {table: count(engine, table) for table in tables}


@pytest.mark.anyio
async def test_valid_output_stores_its_items_with_their_sources(migrated: Engine, clock: Clock) -> None:
    """T-MEM-01."""
    conversation_id, message_ids = seeded_chat(migrated)
    queue = JobQueue(clock=clock)
    job = enqueue(migrated, queue, conversation_id)
    runtime = ScriptedRuntime(VALID)
    leases = Leases()

    finished = await scheduler(migrated, runtime, queue, clock, leases).run_once()

    assert finished is not None
    assert finished.id == job.id
    assert finished.status is JobStatus.COMPLETED
    assert leases.acquired == [("llm", job.id)]
    [request] = runtime.requests
    assert (request.grammar, request.max_length, request.temperature) == (EXTRACTION_GRAMMAR, 1200, 0.3)
    assert "[1] Utente: Ciao! Oggi ho iniziato a lavorare allo Studio Rossi." in request.prompt
    assert "[10] Dario: A domani, allora." in request.prompt
    result = finished.result
    assert result is not None
    assert {key: result[key] for key in ("status", "from_seq", "to_seq", "attempts", "run_attempt")} == {
        "status": "completed",
        "from_seq": 1,
        "to_seq": 10,
        "attempts": 1,
        "run_attempt": 1,
    }
    assert (result["memories_added"], result["episodes_added"]) == (3, 1)
    # A source outside the window, an unknown label, an update without a target and an event outside the window.
    assert (result["dropped_items"], result["deferred_items"]) == (4, 1)

    stored = rows(
        migrated,
        "SELECT id, scope, conversation_id, kind, content, subject_key, value_text, provenance, durability, status, "
        "importance, confidence, expires_at, valid_from, language, version FROM memories ORDER BY content",
    )
    assert [(row["content"], row["provenance"], row["confidence"]) for row in stored] == [
        ("Ha un gatto di nome Briciola.", "inferred", 0.6),
        ("Lavora allo Studio Rossi.", "stated", 0.95),
        ("Questa settimana è molto stanca.", "stated", 0.7),
    ]
    owners = {(row["scope"], row["conversation_id"], row["status"], row["language"], row["version"]) for row in stored}
    assert owners == {("conversation", conversation_id, "active", "it", 1)}
    cat, job_fact, tired = stored
    assert (job_fact["subject_key"], job_fact["value_text"], job_fact["durability"]) == (
        "user.employer",
        "Studio Rossi",
        "lasting",
    )
    assert (tired["durability"], tired["expires_at"]) == ("temporary", to_utc_text(START + timedelta(days=7)))
    assert job_fact["valid_from"] == to_utc_text(START)
    assert set(result["memory_ids"]) == {row["id"] for row in stored}

    sources = rows(
        migrated,
        "SELECT s.memory_id, s.message_id, s.variant_id, s.content_hash, m.active_variant_id, v.content_hash AS hash "
        "FROM memory_sources AS s JOIN messages AS m ON m.id = s.message_id "
        "JOIN message_variants AS v ON v.id = m.active_variant_id",
    )
    by_memory: dict[str, set[str]] = {}
    for source in sources:
        assert (source["variant_id"], source["content_hash"]) == (source["active_variant_id"], source["hash"])
        by_memory.setdefault(source["memory_id"], set()).add(source["message_id"])
    assert by_memory == {
        job_fact["id"]: {message_ids[0]},
        cat["id"]: {message_ids[2], message_ids[3]},
        tired["id"]: {message_ids[4]},
    }
    assert read(migrated, lambda connection: match_memory_ids(connection, "gatti", "it", 10)) == [cat["id"]]

    [episode] = rows(migrated, "SELECT id, title, participants_json, occurred_at, status FROM episodic_memories")
    assert (episode["title"], json.loads(episode["participants_json"]), episode["status"]) == (
        "Primo giorno allo Studio Rossi",
        ["Utente", "Dario"],
        "active",
    )
    episode_sources = rows(migrated, "SELECT episode_id, message_id FROM episodic_sources")
    assert {(row["episode_id"], row["message_id"]) for row in episode_sources} == {
        (episode["id"], message_id) for message_id in message_ids[:2]
    }
    assert count(migrated, "episode_fts") == 1
    assert count(migrated, "character_state_history") == 0, "relationship events wait for the state manager"

    [run] = rows(migrated, "SELECT * FROM memory_extraction_runs")
    assert (run["status"], run["attempt"], run["from_seq"], run["to_seq"], run["error_code"]) == (
        "completed",
        1,
        1,
        10,
        None,
    )
    assert (run["prompt_version"], run["schema_version"], run["raw_output"]) == ("extraction.v2", "1", VALID)
    assert cursor(migrated, conversation_id) == 10
    assert count(migrated, "generation_jobs") == 1, "nothing is left after the window, so no other job is queued"


@pytest.mark.anyio
async def test_invalid_output_changes_nothing_keeps_the_cursor_and_retries(migrated: Engine, clock: Clock) -> None:
    """T-MEM-02."""
    conversation_id, _ = seeded_chat(migrated)
    queue = JobQueue(clock=clock)
    job = enqueue(migrated, queue, conversation_id)
    runtime = ScriptedRuntime(NOT_JSON, output([memory(importance=9)]))
    worker = scheduler(migrated, runtime, queue, clock, Leases())
    empty = stored_state(migrated)

    failed = await worker.run_once()

    assert failed is not None
    assert (failed.id, failed.status, failed.error_code, failed.attempt) == (
        job.id,
        JobStatus.QUEUED,
        "STRUCTURED_OUTPUT_INVALID",
        1,
    )
    assert failed.error_detail == "Il campo «memories.0.importance» non è valido."
    assert failed.not_before == START + timedelta(minutes=1)
    assert len(runtime.requests) == 2, "the first generation and its one repair"
    assert "non è un JSON valido" in runtime.requests[1].prompt
    assert stored_state(migrated) == empty
    assert cursor(migrated, conversation_id) == 0
    [run] = rows(migrated, "SELECT status, attempt, error_code, raw_output FROM memory_extraction_runs")
    assert (run["status"], run["attempt"], run["error_code"]) == ("failed", 1, "STRUCTURED_OUTPUT_INVALID")
    assert json.loads(run["raw_output"])["memories"][0]["importance"] == 9, "the refused output stays for debugging"
    assert count(migrated, "messages") == len(CHAT)

    assert await worker.run_once() is None, "the retry waits for its backoff"

    clock.now = START + timedelta(minutes=1)
    runtime.script(output([memory()]))
    retried = await worker.run_once()

    assert retried is not None
    assert (retried.id, retried.status, retried.attempt) == (job.id, JobStatus.COMPLETED, 2)
    assert retried.result is not None
    assert (retried.result["memories_added"], retried.result["run_attempt"]) == (1, 2)
    [run] = rows(migrated, "SELECT status, attempt, error_code FROM memory_extraction_runs")
    assert (run["status"], run["attempt"], run["error_code"]) == ("completed", 2, None)
    assert cursor(migrated, conversation_id) == 10


@pytest.mark.anyio
async def test_the_same_window_twice_gives_one_result(migrated: Engine, clock: Clock) -> None:
    """T-MEM-03: a second run of the job, and a run of the same window after the cursor went back, generate nothing
    and add nothing."""
    conversation_id, _ = seeded_chat(migrated)
    queue = JobQueue(clock=clock)
    job = enqueue(migrated, queue, conversation_id)
    runtime = ScriptedRuntime(VALID)
    service = MemoryExtractionService(migrated, runtime, queue=queue, clock=clock)
    context = RunContext(lease=None, genkey="genkey")

    first = await service(job, context)
    after_first = stored_state(migrated)
    again = await service(job, context)
    write(
        migrated,
        lambda connection: connection.execute(
            text("UPDATE conversations SET last_extracted_seq = 0 WHERE id = :id"), {"id": conversation_id}
        ),
    )
    replayed = await service(job, context)

    assert len(runtime.requests) == 1, "only the first run generates"
    assert isinstance(first, JobDone)
    assert isinstance(again, JobDone)
    assert isinstance(replayed, JobDone)
    assert first.result is not None
    assert first.result["status"] == "completed"
    assert again.result == {"status": "nothing_to_extract", "run_id": None}
    assert replayed.result == {"status": "already_extracted", "run_id": first.result["run_id"]}
    assert stored_state(migrated) == after_first
    assert count(migrated, "memory_extraction_runs") == 1
    assert cursor(migrated, conversation_id) == 10, "the completed run moves the cursor past its window again"


@pytest.mark.anyio
async def test_extraction_waits_in_image_mode_and_without_a_ready_runtime(migrated: Engine, clock: Clock) -> None:
    """T-MEM-17, scheduler half: the job is not eligible in image mode, and asks for no lease."""
    conversation_id, _ = seeded_chat(migrated)
    queue = JobQueue(clock=clock)
    job = enqueue(migrated, queue, conversation_id)
    runtime = ScriptedRuntime(VALID)
    service = MemoryExtractionService(migrated, runtime, queue=queue, clock=clock)
    leases = Leases()
    for mode in (RequiredMode.IMAGE, RequiredMode.NONE):
        worker = JobScheduler(migrated, queue, {JobKind.MEMORY_EXTRACTION: service}, leases, in_mode(mode))
        assert await worker.run_once() is None

    assert leases.acquired == []
    assert runtime.requests == []
    stored = read(migrated, lambda connection: queue.get(connection, job.id))
    assert stored is not None
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 0)


def test_the_trigger_queues_one_job_when_the_chat_is_due(migrated: Engine, clock: Clock) -> None:
    queue = JobQueue(clock=clock)
    settings = ExtractionSettings()

    def trigger(conversation_id: str) -> Job | None:
        return write(migrated, lambda connection: queue_extraction_if_due(connection, queue, conversation_id, settings))

    quiet = start_chat(migrated)
    for author, line in CHAT[:9]:
        add_message(migrated, quiet, author, line)
    assert trigger(quiet) is None, "nine messages are not a window yet"
    add_message(migrated, quiet, Author.CHARACTER, "Ultimo messaggio.")
    queued = trigger(quiet)
    assert queued is not None
    assert (queued.kind, queued.status) == (JobKind.MEMORY_EXTRACTION, JobStatus.QUEUED)
    assert queued.conversation_id == quiet
    assert trigger(quiet) is None, "one extraction job per chat at a time"

    significant = start_chat(migrated)
    add_message(migrated, significant, Author.USER, "Ricordati che sono allergica alle noci.")
    assert trigger(significant) is None, "a significant event needs two unprocessed messages"
    add_message(migrated, significant, Author.CHARACTER, "Me lo segno.")
    assert trigger(significant) is not None


@pytest.mark.anyio
async def test_a_reply_that_completes_the_tenth_message_queues_the_extraction(migrated: Engine) -> None:
    conversation_id = start_chat(migrated)
    messages = MessageService(migrated)
    replies = ChatInferenceService(migrated, ScriptedRuntime(), EventHub("boot"))
    worker = JobScheduler(migrated, JobQueue(), {JobKind.CHAT_REPLY: replies}, Leases(), lambda: RequiredMode.LLM)

    def extraction_jobs() -> list[RowMapping]:
        return rows(migrated, "SELECT conversation_id FROM generation_jobs WHERE kind = 'memory_extraction'")

    for number in range(1, 6):
        messages.send(conversation_id, f"Messaggio numero {number}.")
        finished = await worker.run_once()
        assert finished is not None
        assert finished.status is JobStatus.COMPLETED
        assert len(extraction_jobs()) == (1 if number == 5 else 0), number

    assert [row["conversation_id"] for row in extraction_jobs()] == [conversation_id]


EMPTY = output([])


class Chat:
    """A seeded chat whose extraction windows run one after the other through the handler, each with its scripted
    output. `message_ids` grow with every message added."""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self.engine = engine
        self.conversation_id, self.message_ids = seeded_chat(engine)
        queue = JobQueue(clock=clock)
        self.job = enqueue(engine, queue, self.conversation_id)
        self.runtime = ScriptedRuntime()
        self.events = EventHub("boot")
        self.service = MemoryExtractionService(engine, self.runtime, queue=queue, events=self.events, clock=clock)

    async def extract(self, answer: str, *turns: tuple[Author, str]) -> dict[str, Any]:
        """Adds `turns` as new messages, runs the next window with `answer` and returns the completed job's result."""
        for author, line in turns:
            self.message_ids.append(add_message(self.engine, self.conversation_id, author, line))
        self.runtime.script(answer)
        done = await self.service(self.job, RunContext(lease=None, genkey="genkey"))
        assert isinstance(done, JobDone)
        assert done.result is not None
        assert done.result["status"] == "completed"
        return dict(done.result)

    def shown(self) -> list[str]:
        """Returns the existing-memory lines of the last prompt, the data after the worked example."""
        section = self.runtime.requests[-1].prompt.rsplit("Ricordi esistenti:\n", 1)[1]
        return section.split("\nEpisodi recenti:", 1)[0].splitlines()

    def memories(self) -> dict[str, RowMapping]:
        """Returns every memory row of the chat by content."""
        found = rows(self.engine, "SELECT * FROM memories WHERE conversation_id = :id", id=self.conversation_id)
        return {row["content"]: row for row in found}


@pytest.mark.anyio
async def test_an_update_supersedes_keeps_history_and_the_prompt_uses_the_new_fact(
    migrated: Engine, clock: Clock
) -> None:
    """T-MEM-10."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory()]))
    old = chat.memories()["Lavora allo Studio Rossi."]
    update = memory(
        relation="update", target_ref="E1", content="Lavora da Verdi & Co.", value="Verdi & Co", source_seqs=[11]
    )

    changed = await chat.extract(
        output([update]),
        (Author.USER, "Ho cambiato lavoro, ora sono da Verdi & Co."),
        (Author.CHARACTER, "Congratulazioni!"),
    )

    assert chat.shown() == ["E1: Lavora allo Studio Rossi."]
    assert (changed["memories_added"], changed["memories_superseded"], changed["dropped_items"]) == (1, 1, 0)
    stored = chat.memories()
    rossi, verdi = stored["Lavora allo Studio Rossi."], stored["Lavora da Verdi & Co."]
    assert (rossi["status"], rossi["version"], rossi["superseded_by_id"]) == ("superseded", 2, verdi["id"])
    assert rossi["valid_to"] == verdi["valid_from"] == to_utc_text(START)
    assert (verdi["status"], verdi["provenance"], verdi["subject_key"], verdi["version"]) == (
        "active",
        "stated",
        "user.employer",
        1,
    )
    [revision] = rows(migrated, "SELECT * FROM memory_revisions")
    assert (revision["memory_id"], revision["version"], revision["actor"], revision["reason"]) == (
        old["id"],
        1,
        "extraction",
        "update",
    )
    assert revision["run_id"] == changed["run_id"]
    snapshot = json.loads(revision["snapshot_json"])
    assert (snapshot["status"], snapshot["content"], snapshot["version"]) == ("active", "Lavora allo Studio Rossi.", 1)
    sources = rows(migrated, "SELECT memory_id, message_id FROM memory_sources")
    assert {(row["memory_id"], row["message_id"]) for row in sources} == {
        (old["id"], chat.message_ids[0]),
        (verdi["id"], chat.message_ids[10]),
    }, "the superseded memory keeps its sources"

    await chat.extract(EMPTY, (Author.USER, "Domani inizio presto."), (Author.CHARACTER, "In bocca al lupo!"))

    assert chat.shown() == ["E1: Lavora da Verdi & Co."], "the next prompt holds the new fact only"


@pytest.mark.anyio
async def test_an_unresolved_contradiction_stays_uncertain_and_both_are_labeled(migrated: Engine, clock: Clock) -> None:
    """T-MEM-11: both memories become uncertain in one conflict group, `memory.conflict` fires for an importance of
    at least 3, and the next prompt labels both `[incerto]`."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory()]))
    contradiction = memory(
        relation="contradiction",
        target_ref="E1",
        content="Lavora allo Studio Bianchi.",
        value="Studio Bianchi",
        source_seqs=[11],
    )

    doubt = await chat.extract(
        output([contradiction]),
        (Author.USER, "Mi ha preso lo Studio Bianchi, credo. O era Rossi?"),
        (Author.CHARACTER, "Non ti seguo più."),
    )

    stored = chat.memories()
    rossi, bianchi = stored["Lavora allo Studio Rossi."], stored["Lavora allo Studio Bianchi."]
    assert rossi["status"] == bianchi["status"] == "uncertain"
    assert rossi["conflict_group_id"] is not None
    assert rossi["conflict_group_id"] == bianchi["conflict_group_id"]
    assert (rossi["version"], doubt["memories_uncertain"], doubt["conflict_ids"]) == (2, 1, [bianchi["id"]])
    [revision] = rows(migrated, "SELECT reason FROM memory_revisions")
    assert revision["reason"] == "contradiction"
    events = chat.events.missed(0, "boot")
    assert events is not None
    assert [(event.type, dict(event.data)) for event in events] == [
        ("memory.conflict", {"memory_id": bianchi["id"], "conversation_id": chat.conversation_id})
    ]

    await chat.extract(EMPTY, (Author.USER, "Ne riparliamo domani."), (Author.CHARACTER, "Va bene."))

    assert {line.split(": ", 1)[1] for line in chat.shown()} == {
        "[incerto] Lavora allo Studio Rossi.",
        "[incerto] Lavora allo Studio Bianchi.",
    }


@pytest.mark.anyio
async def test_a_temporary_memory_expires_and_does_not_replace_a_lasting_one(migrated: Engine, clock: Clock) -> None:
    """T-MEM-12."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory(content="Vive a Milano.", subject="user.city", value="Milano")]))
    this_week = memory(
        relation="update",
        target_ref="E1",
        content="Questa settimana è a Roma.",
        subject="user.city",
        value="Roma",
        durability="temporary",
        importance=2,
        source_seqs=[11],
    )

    week = await chat.extract(
        output([this_week]),
        (Author.USER, "Questa settimana sono a Roma per lavoro."),
        (Author.CHARACTER, "Goditela!"),
    )

    stored = chat.memories()
    milano, roma = stored["Vive a Milano."], stored["Questa settimana è a Roma."]
    assert (milano["status"], milano["version"]) == ("active", 1)
    assert (roma["status"], roma["durability"], roma["expires_at"]) == (
        "active",
        "temporary",
        to_utc_text(START + timedelta(days=7)),
    )
    assert (week["memories_added"], week["memories_superseded"], week["memories_uncertain"]) == (1, 0, 0)

    clock.now = START + timedelta(days=7)
    await chat.extract(EMPTY, (Author.USER, "Sono tornata a casa."), (Author.CHARACTER, "Bentornata!"))

    stored = chat.memories()
    assert (stored["Vive a Milano."]["status"], stored["Questa settimana è a Roma."]["status"]) == ("active", "expired")
    [revision] = rows(migrated, "SELECT memory_id, actor, reason FROM memory_revisions")
    assert (revision["memory_id"], revision["actor"], revision["reason"]) == (roma["id"], "consistency", "expired")
    assert chat.shown() == ["E1: Vive a Milano."]


@pytest.mark.anyio
async def test_an_explicit_user_correction_wins_and_is_confirmed(migrated: Engine, clock: Clock) -> None:
    """T-MEM-13."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory(content="Ha un gatto.", subject="user.pets", value="un gatto", source_seqs=[3])]))
    correction = memory(
        relation="correction",
        target_ref="E1",
        content="Ha due gatti.",
        subject="user.pets",
        value="due gatti",
        confidence="medium",
        source_seqs=[11],
    )

    fixed = await chat.extract(
        output([correction]),
        (Author.USER, "No, non ho detto questo: ho due gatti."),
        (Author.CHARACTER, "Scusa, me lo segno."),
    )

    stored = chat.memories()
    one, two = stored["Ha un gatto."], stored["Ha due gatti."]
    assert (one["status"], one["superseded_by_id"]) == ("superseded", two["id"])
    assert (two["status"], two["provenance"], two["confidence"]) == ("active", "user_confirmed", 1.0)
    assert fixed["memories_superseded"] == 1
    [revision] = rows(migrated, "SELECT reason FROM memory_revisions")
    assert revision["reason"] == "correction"


@pytest.mark.anyio
async def test_an_identity_candidate_is_not_applied_until_the_user_applies_it(migrated: Engine, clock: Clock) -> None:
    """T-CON-05, extraction half: a candidate is stored as `identity_candidate`, never changes the character's card or
    the memory it targets, and never enters a prompt. Applying it ("Applica al profilo") is the Memory Studio's."""
    chat = Chat(migrated, clock)
    card = [dict(row) for row in rows(migrated, "SELECT * FROM characters")]
    age = memory(
        kind="identity_candidate",
        content="Dario dice di avere 40 anni.",
        subject="character.age",
        value="40",
        provenance="fictional",
        source_seqs=[2],
    )
    await chat.extract(output([memory(), age]))
    job = memory(
        relation="update",
        target_ref="E1",
        kind="identity_candidate",
        content="Dario lavora come cartografo.",
        subject="character.job",
        value="cartografo",
        source_seqs=[12],
    )

    proposed = await chat.extract(
        output([job]),
        (Author.USER, "E tu che lavoro fai?"),
        (Author.CHARACTER, "Faccio il cartografo, sai?"),
    )

    assert chat.shown() == ["E1: Lavora allo Studio Rossi."], "a candidate never enters a prompt"
    assert (proposed["memories_added"], proposed["memories_superseded"]) == (1, 0)
    stored = chat.memories()
    assert (stored["Lavora allo Studio Rossi."]["status"], stored["Lavora allo Studio Rossi."]["version"]) == (
        "active",
        1,
    )
    candidates = {content: row["kind"] for content, row in stored.items() if content.startswith("Dario")}
    assert candidates == {
        "Dario dice di avere 40 anni.": "identity_candidate",
        "Dario lavora come cartografo.": "identity_candidate",
    }
    assert count(migrated, "memory_revisions") == 0
    assert [dict(row) for row in rows(migrated, "SELECT * FROM characters")] == card
    assert count(migrated, "character_versions") == 0
