"""T-MEM-16 and the scheduler half of T-MEM-17: progressive summaries on a real database.

The summary job runs through the real JobScheduler under a granted llm lease, or by calling the handler directly, with
SummaryService and a scripted in-process text runtime. Small settings (chunks of 20, a tail of 10) keep the chats short.
Checks the ranges and their contiguity, the cursor, the rollup of six checkpoints into one of the next level with the
merged rows kept, a second version that supersedes the first, rows that cannot be overwritten, a run that finds its
messages changed, invalid output that writes nothing, and the job that waits in image mode.
"""

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, Engine, RowMapping, text
from sqlalchemy.exc import IntegrityError

from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import Author, MessageStatus, VariantSource, VariantStatus, content_hash
from lyrveil.jobs.handlers import INTERNAL, JobDone, JobFailedError, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.memory.summaries import SummarySettings
from lyrveil.orchestration.client import Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence import messages as message_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.prompts.structured import STRUCTURED_OUTPUT_INVALID
from lyrveil.services.conversations import ConversationService
from lyrveil.services.summaries import SUMMARY_GRAMMAR, SummaryService, queue_summary_if_due

START = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
SETTINGS = SummarySettings(chunk_messages=20, recent_tail_messages=10)


class Clock:
    """A clock the test moves by hand, shared by the queue and the service."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class ScriptedRuntime:
    """A TextRuntime whose `generate` answers the scripted texts in order and keeps every request. `before_answer`,
    when set, runs first, so a test can change the database while the model "runs"."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.requests: list[TextRequest] = []
        self.before_answer: Callable[[], None] | None = None

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        yield TextChunk("", "stop", "")

    async def generate(self, request: TextRequest) -> str:
        self.requests.append(request)
        if self.before_answer is not None:
            self.before_answer()
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


def add_message(engine: Engine, conversation_id: str, author: Author, content: str) -> None:
    now = to_utc_text(START)

    def work(connection: Connection) -> None:
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

    write(engine, work)


def chat_of(engine: Engine, count: int) -> str:
    """Starts a chat with `count` complete messages, alternating user and character."""
    conversation_id = start_chat(engine)
    for number in range(1, count + 1):
        author = Author.USER if number % 2 else Author.CHARACTER
        add_message(engine, conversation_id, author, f"Messaggio numero {number} della storia.")
    return conversation_id


def summary_output(label: str, seqs: list[int] | None = None) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "text": f"Riassunto {label} della storia fino a qui.",
            "events": [f"Evento {label}"],
            "decisions": [],
            "relationship_changes": [],
            "unresolved": [],
            "key_facts": [f"Fatto {label}"],
            "source_seqs": seqs if seqs is not None else [1],
        },
        ensure_ascii=False,
    )


def in_mode(mode: RequiredMode) -> Callable[[], RequiredMode]:
    """Returns a `current_mode` for JobScheduler that always reports `mode`."""
    return lambda: mode


def scheduler(engine: Engine, runtime: ScriptedRuntime, queue: JobQueue, clock: Clock, leases: Leases) -> JobScheduler:
    service = SummaryService(engine, runtime, queue=queue, settings=SETTINGS, clock=clock)
    return JobScheduler(engine, queue, {JobKind.SUMMARY_CHECKPOINT: service}, leases, lambda: RequiredMode.LLM)


def trigger(engine: Engine, queue: JobQueue, conversation_id: str) -> Job | None:
    return write(engine, lambda connection: queue_summary_if_due(connection, queue, conversation_id, SETTINGS))


async def drain(worker: JobScheduler) -> list[Job]:
    """Runs queued jobs until none is left and returns the finished ones."""
    finished: list[Job] = []
    while (job := await worker.run_once()) is not None:
        finished.append(job)
    return finished


def rows(engine: Engine, sql: str, **params: object) -> list[RowMapping]:
    return read(engine, lambda connection: list(connection.execute(text(sql), params).mappings().all()))


def checkpoints(engine: Engine, where: str = "1 = 1") -> list[RowMapping]:
    return rows(engine, f"SELECT * FROM conversation_summaries WHERE {where} ORDER BY level, from_seq, version")


def cursor(engine: Engine, conversation_id: str) -> int:
    found = rows(engine, "SELECT last_summarized_seq FROM conversations WHERE id = :id", id=conversation_id)
    return int(found[0]["last_summarized_seq"])


@pytest.mark.anyio
async def test_a_chunk_is_summarized_with_its_range_and_moves_the_cursor(migrated: Engine, clock: Clock) -> None:
    """T-MEM-16: the range, the cursor and the cited messages. The newest ten messages stay out."""
    conversation_id = chat_of(migrated, 30)
    queue = JobQueue(clock=clock)
    job = trigger(migrated, queue, conversation_id)
    assert job is not None
    assert (job.kind, job.status) == (JobKind.SUMMARY_CHECKPOINT, JobStatus.QUEUED)
    assert trigger(migrated, queue, conversation_id) is None, "one summary job per chat at a time"
    runtime = ScriptedRuntime(summary_output("A", [2, 5, 5, 99]))
    leases = Leases()

    [finished] = await drain(scheduler(migrated, runtime, queue, clock, leases))

    assert finished.id == job.id
    assert finished.status is JobStatus.COMPLETED
    assert leases.acquired == [("llm", job.id)]
    [request] = runtime.requests
    assert request.grammar == SUMMARY_GRAMMAR
    assert (request.max_length, request.temperature) == (700, 0.3)
    assert "[1] Utente: Messaggio numero 1 della storia." in request.prompt
    assert "[20] Dario: Messaggio numero 20 della storia." in request.prompt
    assert "[21]" not in request.prompt, "the recent tail is not summarized"
    [stored] = checkpoints(migrated)
    assert (stored["level"], stored["from_seq"], stored["to_seq"], stored["version"], stored["status"]) == (
        0,
        1,
        20,
        1,
        "active",
    )
    assert stored["text"] == "Riassunto A della storia fino a qui."
    assert stored["token_count"] == 7, "counted by the runtime"
    assert stored["prompt_version"] == "summary.v1"
    content = json.loads(stored["content_json"])
    assert content["source_seqs"] == [2, 5], "a cited message outside the range is dropped"
    assert content["key_facts"] == ["Fatto A"]
    assert cursor(migrated, conversation_id) == 20
    assert trigger(migrated, queue, conversation_id) is None, "nothing more is due"


@pytest.mark.anyio
async def test_consecutive_chunks_cover_contiguous_ranges_and_six_of_them_make_a_rollup(
    migrated: Engine, clock: Clock
) -> None:
    """T-MEM-16: contiguity, rollup of the oldest four of six checkpoints, and the merged rows left as they were."""
    conversation_id = chat_of(migrated, 130)
    queue = JobQueue(clock=clock)
    assert trigger(migrated, queue, conversation_id) is not None
    runtime = ScriptedRuntime(*(summary_output(label) for label in "ABCDEF"), summary_output("ROLLUP", [1, 80]))
    worker = scheduler(migrated, runtime, queue, clock, Leases())

    finished = await drain(worker)

    assert [job.status for job in finished] == [JobStatus.COMPLETED] * 7
    level_zero = checkpoints(migrated, "level = 0")
    assert [(row["from_seq"], row["to_seq"]) for row in level_zero] == [
        (1, 20),
        (21, 40),
        (41, 60),
        (61, 80),
        (81, 100),
        (101, 120),
    ]
    assert all(row["status"] == "active" and row["version"] == 1 for row in level_zero)
    assert cursor(migrated, conversation_id) == 120
    [rollup] = checkpoints(migrated, "level = 1")
    assert (rollup["from_seq"], rollup["to_seq"], rollup["version"], rollup["status"]) == (1, 80, 1, "active")
    assert json.loads(rollup["content_json"])["source_seqs"] == [1, 80]
    sources = [row["id"] for row in level_zero[:4]]
    request = runtime.requests[-1]
    assert [part in request.prompt for part in ("dei messaggi da 1 a 20", "dei messaggi da 61 a 80")] == [True, True]
    assert "dei messaggi da 81 a 100" not in request.prompt, "only the oldest four are merged"
    assert [row["id"] for row in checkpoints(migrated, "level = 0")[:4]] == sources
    assert [row["text"] for row in level_zero] == [f"Riassunto {label} della storia fino a qui." for label in "ABCDEF"]
    assert trigger(migrated, queue, conversation_id) is None, "two checkpoints left do not make another rollup"


@pytest.mark.anyio
async def test_a_range_summarized_again_gets_a_new_version_and_the_old_one_stays(
    migrated: Engine, clock: Clock
) -> None:
    """T-MEM-16: versions and no overwrite."""
    conversation_id = chat_of(migrated, 30)
    queue = JobQueue(clock=clock)
    assert trigger(migrated, queue, conversation_id) is not None
    runtime = ScriptedRuntime(summary_output("A"), summary_output("B"))
    worker = scheduler(migrated, runtime, queue, clock, Leases())
    await drain(worker)
    [first] = checkpoints(migrated)

    write(
        migrated,
        lambda connection: queue.enqueue(
            connection,
            JobKind.SUMMARY_CHECKPOINT,
            {"from_seq": 1, "to_seq": 20},
            conversation_id=conversation_id,
        ),
    )
    [finished] = await drain(worker)

    assert finished.result is not None
    assert (finished.result["kind"], finished.result["version"]) == ("resummarize", 2)
    old, new = checkpoints(migrated)
    assert (old["id"], old["version"], old["status"]) == (first["id"], 1, "superseded")
    assert old["text"] == first["text"] == "Riassunto A della storia fino a qui."
    assert (new["version"], new["status"], new["text"]) == (2, "active", "Riassunto B della storia fino a qui.")
    assert cursor(migrated, conversation_id) == 20, "summarizing a range again leaves the cursor where it is"

    with pytest.raises(IntegrityError, match="never overwritten"):
        write(
            migrated,
            lambda connection: connection.execute(
                text("UPDATE conversation_summaries SET text = 'altro' WHERE id = :id"), {"id": new["id"]}
            ),
        )
    write(
        migrated,
        lambda connection: connection.execute(
            text("UPDATE conversation_summaries SET status = 'invalidated' WHERE id = :id"), {"id": new["id"]}
        ),
    )
    assert checkpoints(migrated, "status = 'invalidated'")[0]["text"] == new["text"], "only the status can change"


@pytest.mark.anyio
async def test_a_run_whose_messages_changed_writes_nothing(migrated: Engine, clock: Clock) -> None:
    """T-MEM-16: a checkpoint never records a range other than the one it summarized."""
    conversation_id = chat_of(migrated, 30)
    queue = JobQueue(clock=clock)
    job = trigger(migrated, queue, conversation_id)
    assert job is not None
    runtime = ScriptedRuntime(summary_output("A"))

    def edit_a_message() -> None:
        write(
            migrated,
            lambda connection: connection.execute(
                text("UPDATE message_variants SET content_hash = 'cambiato' WHERE content LIKE 'Messaggio numero 3 %'")
            ),
        )

    runtime.before_answer = edit_a_message
    service = SummaryService(migrated, runtime, queue=queue, settings=SETTINGS, clock=clock)

    done = await service(job, RunContext(lease=None, genkey="genkey"))

    assert isinstance(done, JobDone)
    assert done.result is not None
    assert done.result["status"] == "superseded"
    assert checkpoints(migrated) == []
    assert cursor(migrated, conversation_id) == 0


@pytest.mark.anyio
async def test_invalid_output_writes_nothing_and_asks_for_a_retry(migrated: Engine, clock: Clock) -> None:
    conversation_id = chat_of(migrated, 30)
    queue = JobQueue(clock=clock)
    job = trigger(migrated, queue, conversation_id)
    assert job is not None
    runtime = ScriptedRuntime("non è JSON", "ancora no")
    service = SummaryService(migrated, runtime, queue=queue, settings=SETTINGS, clock=clock)

    with pytest.raises(JobFailedError) as failure:
        await service(job, RunContext(lease=None, genkey="genkey"))

    assert (failure.value.code, failure.value.retryable) == (STRUCTURED_OUTPUT_INVALID, True)
    assert len(runtime.requests) == 2, "the output and one repair"
    assert checkpoints(migrated) == []
    assert cursor(migrated, conversation_id) == 0


@pytest.mark.anyio
async def test_a_payload_that_names_no_valid_range_fails_for_good(migrated: Engine, clock: Clock) -> None:
    conversation_id = chat_of(migrated, 4)
    queue = JobQueue(clock=clock)
    job = write(
        migrated,
        lambda connection: queue.enqueue(
            connection, JobKind.SUMMARY_CHECKPOINT, {"from_seq": 9, "to_seq": 3}, conversation_id=conversation_id
        ),
    )
    service = SummaryService(migrated, ScriptedRuntime(), queue=queue, settings=SETTINGS, clock=clock)

    with pytest.raises(JobFailedError) as failure:
        await service(job, RunContext(lease=None, genkey="genkey"))

    assert (failure.value.code, failure.value.retryable) == (INTERNAL, False)


@pytest.mark.anyio
async def test_summaries_wait_in_image_mode_and_without_a_ready_runtime(migrated: Engine, clock: Clock) -> None:
    """T-MEM-17, scheduler half: the job is not eligible in image mode, and asks for no lease."""
    conversation_id = chat_of(migrated, 30)
    queue = JobQueue(clock=clock)
    job = trigger(migrated, queue, conversation_id)
    assert job is not None
    runtime = ScriptedRuntime(summary_output("A"))
    service = SummaryService(migrated, runtime, queue=queue, settings=SETTINGS, clock=clock)
    leases = Leases()
    for mode in (RequiredMode.IMAGE, RequiredMode.NONE):
        worker = JobScheduler(migrated, queue, {JobKind.SUMMARY_CHECKPOINT: service}, leases, in_mode(mode))
        assert await worker.run_once() is None

    assert leases.acquired == []
    assert runtime.requests == []
    stored = read(migrated, lambda connection: queue.get(connection, job.id))
    assert stored is not None
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 0)
    assert checkpoints(migrated) == []


def test_the_table_refuses_a_second_row_of_the_same_range_and_version(migrated: Engine) -> None:
    conversation_id = chat_of(migrated, 2)
    columns = (
        "id, conversation_id, level, from_seq, to_seq, version, status, content_json, text, source_hash, "
        "prompt_version, token_count, created_at"
    )

    def insert(connection: Connection, checkpoint_id: str, version: int, status: str = "active") -> None:
        connection.execute(
            text(
                f"INSERT INTO conversation_summaries ({columns}) VALUES (:id, :conversation, 0, 1, 2, :version, "
                ":status, '{}', 'x', 'h', 'summary.v1', 1, '2026-11-07T10:00:00.000Z')"
            ),
            {"id": checkpoint_id, "conversation": conversation_id, "version": version, "status": status},
        )

    write(migrated, lambda connection: insert(connection, "a", 1))
    write(migrated, lambda connection: insert(connection, "b", 2, "superseded"))
    with pytest.raises(IntegrityError):
        write(migrated, lambda connection: insert(connection, "c", 2))
    with pytest.raises(IntegrityError):
        write(migrated, lambda connection: insert(connection, "d", 3, "gone"))
    assert [row["id"] for row in checkpoints(migrated)] == ["a", "b"]
