"""T-MEM-21: ContextBuilder on a migrated database, with FtsMemoryRetriever, stored checkpoints and a full card.

The text runtime is a fake whose tokenizer counts `per_word` tokens per whitespace-separated word, so a test can make
the same chat cost more and see the runtime's counts decide. Checks the budgets at 8192 and 4096, the reduction order
under pressure, the blocks that are never removed, and a reply built through ChatInferenceService.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, Engine, text

from lyrveil.context.builder import (
    EXAMPLES,
    HISTORY_FLOOR_TOKENS,
    MEMORIES,
    MEMORY_MAX_TOKENS,
    REDUCTION_ORDER,
    SUMMARIES,
    Assembly,
    ContextBuilder,
    ContextRequest,
)
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import Author, MessageStatus, VariantSource, VariantStatus, content_hash
from lyrveil.events.hub import EventHub
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.memory.text import fingerprint
from lyrveil.orchestration.client import Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import messages as message_repository
from lyrveil.persistence import summaries as summary_repository
from lyrveil.persistence.memory_index import index_memory
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.persistence.summaries import NewCheckpoint
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.prompts.formatter import formatter_for
from lyrveil.services.chat_inference import ChatInferenceService, load_context_request
from lyrveil.services.conversations import ConversationService
from lyrveil.services.memory_retrieval import FtsMemoryRetriever
from lyrveil.services.messages import MessageService

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
STAMP = to_utc_text(NOW)
MESSAGE_WORDS = 25
SUMMARY_WORDS = 140
QUESTION = "Ti ricordi la mappa del porto vecchio e la luce del faro?"
KINDS = ("user_fact", "user_preference", "decision", "narrative_event")

CARD = CharacterCard.model_validate(
    {
        "identity": {
            "first_name": "Giulia",
            "last_name": "Rossi",
            "age": 29,
            "grammatical_gender": "femminile",
            "narrative_identity": "Restaura mappe antiche a Genova.",
            "biography": "Cresciuta sopra una bottega di cornici, ha imparato il mestiere dal nonno.",
            "occupation": "restauratrice",
            "setting": "Genova, oggi",
            "interests": ["mappe antiche", "gatti di porto"],
            "background": "Ha lasciato la gilda dei cartografi.",
        },
        "traits": [
            {"category": "main_trait", "description": "Curiosa e testarda. Fa domande su tutto."},
            {"category": "main_trait", "description": "Ironica, mai crudele"},
            {"category": "value", "description": "La lealtà"},
            {"category": "fear", "description": "Il mare in tempesta", "locked": True},
            {"category": "habit", "description": "Beve caffè troppo forte"},
            {"category": "humor", "description": "Battute asciutte"},
        ],
        "communication": {
            "message_length": "medi",
            "examples": [{"character": " ".join([f"esempio{number}"] + ["parola"] * 49)} for number in range(1, 9)],
        },
    }
)
# What is never removed (docs/memory-engine.md, Algorithm), as the prompt writes it.
MANDATORY = (
    "Stai interpretando Giulia Rossi in una chat privata con Utente.",
    "Giulia Rossi, 29 anni. Restaura mappe antiche a Genova.",
    "Parli di te al femminile.",
    "Carattere: Curiosa e testarda. Fa domande su tutto.; Ironica, mai crudele",
    "Valori: La lealtà",
    "Paure: Il mare in tempesta",
    "Come scrivi in chat:\nRegistro: informale. Lunghezza: medi. Domande: a volte.",
    "Ricorda: sei Giulia Rossi e hai 29 anni. Parli di te al femminile.",
    f"[Messaggio di Utente]\n{QUESTION}",
)


class CountingRuntime:
    """A TextRuntime with a window of `window` tokens whose tokenizer counts `per_word` tokens per word, plus one
    start token with `special`. Keeps every text counted with `special` and every request streamed."""

    def __init__(self, window: int, per_word: int = 1) -> None:
        self.window = window
        self.per_word = per_word
        self.whole: list[str] = []
        self.requests: list[TextRequest] = []

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        if special:
            self.whole.append(text)
        return len(text.split()) * self.per_word + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        self.requests.append(request)
        yield TextChunk("Va bene.")
        yield TextChunk("", "stop", "Va bene.")

    async def generate(self, request: TextRequest) -> str:
        return "Va bene."

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=self.window)


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


def add_message(engine: Engine, conversation_id: str, author: Author, content: str) -> None:
    def work(connection: Connection) -> None:
        message_id, variant_id = new_id(), new_id()
        message_repository.insert_message(
            connection,
            message_id=message_id,
            conversation_id=conversation_id,
            seq=message_repository.take_seq(connection, conversation_id, STAMP),
            client_message_id=None,
            author=author,
            status=MessageStatus.COMPLETE,
            read_state="sent" if author is Author.USER else None,
            content=content,
            now=STAMP,
        )
        message_repository.insert_variant(
            connection,
            variant_id=variant_id,
            message_id=message_id,
            content=content,
            content_hash=content_hash(content),
            source=VariantSource.USER if author is Author.USER else VariantSource.GENERATED,
            status=VariantStatus.COMPLETE,
            now=STAMP,
        )
        message_repository.update_message(connection, message_id, active_variant_id=variant_id)

    write(engine, work)


def add_memory(connection: Connection, character_id: str, conversation_id: str, number: int) -> None:
    """Adds an indexed memory of about thirty words that the question finds by its words."""
    memory_id = new_id()
    content = (
        f"Memoria {number}: Anna ricorda la mappa del porto e il faro, con il dettaglio numero {number} "
        + " ".join(["ricordo"] * 15)
    )
    connection.execute(
        text(
            "INSERT INTO memories (id, character_id, scope, conversation_id, kind, content, provenance, durability, "
            "status, importance, confidence, locked, fingerprint, language, created_at, updated_at) VALUES (:id, "
            ":character_id, 'conversation', :conversation_id, :kind, :content, 'stated', 'lasting', 'active', 3, "
            "0.95, 0, :fingerprint, 'it', :now, :now)"
        ),
        {
            "id": memory_id,
            "character_id": character_id,
            "conversation_id": conversation_id,
            "kind": KINDS[number % len(KINDS)],
            "content": content,
            "fingerprint": fingerprint(content, "it"),
            "now": STAMP,
        },
    )
    index_memory(connection, memory_id, content, None, "it")


def seed(engine: Engine, messages: int = 139) -> str:
    """Returns a chat of Giulia with `messages` messages (the last odd one is the question), 24 memories and four
    level-0 checkpoints of 140 tokens over messages 1 to 40."""
    character_id = new_id()

    def create(connection: Connection) -> None:
        character_repository.insert(connection, character_id=character_id, card=CARD, now=STAMP)

    write(engine, create)
    conversation_id = ConversationService(engine).create(character_id).id
    filler = " ".join(["chiacchiera"] * (MESSAGE_WORDS - 2))
    for number in range(1, messages + 1):
        author = Author.USER if number % 2 else Author.CHARACTER
        last_question = number == messages and author is Author.USER
        add_message(engine, conversation_id, author, QUESTION if last_question else f"Messaggio {number}: {filler}")

    def remember(connection: Connection) -> None:
        for index, start in enumerate(range(1, 41, 10)):
            checkpoint = NewCheckpoint(
                id=new_id(),
                conversation_id=conversation_id,
                level=0,
                from_seq=start,
                to_seq=start + 9,
                version=1,
                content={"events": [], "decisions": [], "relationship_changes": [], "unresolved": [], "key_facts": []},
                text=f"Riassunto{index} " + " ".join(["evento"] * (SUMMARY_WORDS - 1)),
                source_hash=f"hash-{index}",
                prompt_version="summary.v1",
                token_count=SUMMARY_WORDS,
            )
            summary_repository.insert_checkpoint(connection, checkpoint, STAMP)
        summary_repository.advance_cursor(connection, conversation_id, 40)
        for number in range(24):
            add_memory(connection, character_id, conversation_id, number)

    write(engine, remember)
    return conversation_id


async def assemble(
    engine: Engine, conversation_id: str, window: int, per_word: int = 1
) -> tuple[Assembly, CountingRuntime]:
    """Builds the prompt of the next reply of the chat, as ChatInferenceService loads it."""
    runtime = CountingRuntime(window, per_word)

    def load(connection: Connection) -> ContextRequest | None:
        before: int = connection.execute(
            text("SELECT next_seq FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).scalar_one()
        return load_context_request(connection, conversation_id, before, user=UserProfile(), now=NOW)

    wanted = read(engine, load)
    assert wanted is not None
    builder = ContextBuilder(runtime, FtsMemoryRetriever(engine))
    return await builder.build(wanted, formatter_for("gemma4"), 400), runtime


def assert_documented_order(assembly: Assembly) -> None:
    """The reductions came in the documented order, and each one before the last applied was spent."""
    applied = [REDUCTION_ORDER.index(name) for name in assembly.reductions]
    assert applied == sorted(applied)
    spent = {
        EXAMPLES: assembly.example_count == 0,
        SUMMARIES: not assembly.summary_ids,
        MEMORIES: not assembly.memories,
    }
    for name in REDUCTION_ORDER[: max(applied)]:
        assert spent.get(name, True), f"{name} was not spent before a later reduction"


@pytest.mark.anyio
async def test_budgets_at_8192_and_4096_give_each_block_its_target_and_the_history_the_rest(migrated: Engine) -> None:
    chat = seed(migrated)
    wide, _ = await assemble(migrated, chat, 8192)
    narrow, _ = await assemble(migrated, chat, 4096)

    for assembly, window in ((wide, 8192), (narrow, 4096)):
        assert (assembly.context_limit, assembly.max_length) == (window, 240)
        assert assembly.available == window - 240 - 64
        assert assembly.fits
        assert assembly.reductions == ()
        assert assembly.example_count == 8
        assert assembly.blocks["communication"] <= 500
        assert len(assembly.summary_ids) == 4, "four checkpoints of 140 fit the 600 target"
        assert assembly.memories
        assert assembly.history_seqs == tuple(range(assembly.history_seqs[0], 139))
        for line in MANDATORY:
            assert line in assembly.prompt.text
        assert "Interessi: mappe antiche, gatti di porto." in assembly.prompt.prefix
        assert all(item.line in assembly.prompt.text for item in assembly.memories)
    # 8192: every message after the summaries fits, and the room left widens block 5 towards its maximum.
    assert wide.history_seqs[0] == 41
    assert wide.gaps == ()
    assert len(wide.memories) > len(narrow.memories)
    assert wide.blocks["memories"] <= MEMORY_MAX_TOKENS
    # 4096: the history is what is left, from the newest message back, and the messages between the summaries and it
    # are a logged gap. It takes nearly all of that room; the turn prices only err high.
    assert narrow.history_seqs[0] > 41
    assert narrow.gaps == ((41, narrow.history_seqs[0] - 1),)
    assert narrow.available - narrow.token_total < narrow.context_limit * 3 // 100


@pytest.mark.anyio
async def test_the_runtime_counts_decide_what_fits_and_the_whole_prompt_is_counted(migrated: Engine) -> None:
    chat = seed(migrated)
    cheap, cheap_runtime = await assemble(migrated, chat, 8192, per_word=1)
    costly, costly_runtime = await assemble(migrated, chat, 8192, per_word=2)

    assert len(costly.history_seqs) < len(cheap.history_seqs), "twice the tokens per word leave room for fewer turns"
    for assembly, runtime in ((cheap, cheap_runtime), (costly, costly_runtime)):
        assert assembly.prompt.text in runtime.whole, "the sent prompt was counted whole, with the start token"
        assert assembly.token_total == await runtime.count_tokens(assembly.prompt.text, special=True)
        assert assembly.token_total <= assembly.available


@pytest.mark.anyio
@pytest.mark.parametrize(("window", "per_word"), [(4096, 3), (1536, 1)])
async def test_reductions_follow_the_documented_order_and_keep_what_is_never_removed(
    migrated: Engine, window: int, per_word: int
) -> None:
    chat = seed(migrated)
    assembly, _ = await assemble(migrated, chat, window, per_word)

    assert assembly.fits
    assert assembly.reductions[:2] == (EXAMPLES, SUMMARIES)
    assert_documented_order(assembly)
    for line in MANDATORY:
        assert line in assembly.prompt.text, line
    if "identity" in assembly.reductions:
        # The interests go first among the optional parts; the locked fear is in MANDATORY.
        assert "Interessi:" not in assembly.prompt.text
    if "history_below_floor" not in assembly.reductions:
        # The history keeps its floor: what fits in 800 tokens, so within one turn of it.
        assert assembly.blocks["history"] > HISTORY_FLOOR_TOKENS - 2 * MESSAGE_WORDS * per_word


@pytest.mark.anyio
async def test_a_reply_is_built_by_the_context_builder_and_records_the_memories_it_used(migrated: Engine) -> None:
    chat = seed(migrated, messages=60)
    runtime = CountingRuntime(8192)
    service = ChatInferenceService(migrated, runtime, EventHub("boot"), clock=lambda: NOW)
    scheduler = JobScheduler(migrated, JobQueue(), {JobKind.CHAT_REPLY: service}, Leases(), lambda: RequiredMode.LLM)
    MessageService(migrated).send(chat, QUESTION)
    finished = await scheduler.run_once()
    assert finished is not None
    assert finished.status is JobStatus.COMPLETED

    (request,) = runtime.requests
    assert (request.max_context_length, request.max_length) == (8192, 240)
    assert "Riassunto di quello che è successo prima:\nRiassunto0 " in request.prompt
    assert "Ricordi pertinenti:\n- [sicuro] Memoria " in request.prompt
    assert "Ricorda: sei Giulia Rossi e hai 29 anni." in request.prompt
    used = read(
        migrated,
        lambda connection: connection.execute(
            text("SELECT count(*) FROM memories WHERE use_count = 1 AND last_used_at = :now"), {"now": STAMP}
        ).scalar_one(),
    )
    shown = request.prompt.count("- [sicuro] Memoria ")
    assert used == shown > 0
