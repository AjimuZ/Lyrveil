"""T-CON-03, the extraction half of T-CON-06 and the database half of T-MEM-23: the Character State Manager on a
migrated SQLite file.

Extraction runs call MemoryExtractionService directly with a scripted in-process text runtime, as
test_memory_extraction.py does. Checks that a run's relationship events and state update become bounded history rows
with a reason and sources, and a state row equal to the replay of those rows; that the next extraction prompt and the
reply's block 4 show the state; that a change in one chat leaves the other chat's rows, state and prompt as they were;
and that random invalidations by message keep the stored state equal to a replay of what remains.
"""

import json
import random
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Connection, Engine, text

from lyrveil.consistency.state import DAY_LIMIT, INITIAL_VALUES, RUN_LIMIT, UPDATE_REASONS, RelationshipState, replay
from lyrveil.context.builder import StateText
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.memory_extraction import RelationshipEvent, StateUpdate
from lyrveil.domain.messages import Author, MessageStatus, VariantSource, VariantStatus, content_hash
from lyrveil.jobs.handlers import JobDone, RunContext
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.memory.window import ExtractionSettings
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import messages as message_repository
from lyrveil.persistence import relationship as state_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.persistence.relationship import StoredState
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.services.character_state import CharacterStateManager, StateRun
from lyrveil.services.chat_inference import load_context_request
from lyrveil.services.conversations import ConversationService
from lyrveil.services.memory_extraction import MemoryExtractionService, queue_extraction_if_due

ROME = ZoneInfo("Europe/Rome")
START = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
STAMP = to_utc_text(START)
CARD = CharacterCard.model_validate({"identity": {"first_name": "Dario", "last_name": "Venturi", "age": 34}})
CHAT = (
    (Author.USER, "Ciao! Oggi è stata una giornata lunga al lavoro."),
    (Author.CHARACTER, "Raccontami tutto."),
    (Author.USER, "Il capo mi ha fatto rifare il progetto."),
    (Author.CHARACTER, "Che fatica. Come stai adesso?"),
    (Author.USER, "Meglio, grazie. Con te mi sento tranquilla."),
    (Author.CHARACTER, "Mi fa piacere."),
    (Author.USER, "Scusa se ieri sono stata brusca."),
    (Author.CHARACTER, "Non preoccuparti."),
    (Author.USER, "Domani ti dico com'è andata con Marta."),
    (Author.CHARACTER, "Ci conto."),
)
THREAD = "Deve dirgli com'è andata con Marta."
BANDS_AT_START = (
    "Il vostro rapporto: vi siete appena conosciuti, sei prudente con Utente, parli poco di te, nessuna tensione."
)


class ScriptedRuntime:
    """A TextRuntime whose `generate` answers the scripted texts in order and keeps every request."""

    def __init__(self) -> None:
        self.outputs: list[str] = []
        self.requests: list[TextRequest] = []

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        yield TextChunk("", "stop", "")

    async def generate(self, request: TextRequest) -> str:
        self.requests.append(request)
        return self.outputs.pop(0)

    async def abort(self, genkey: str) -> bool:
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


def new_character(engine: Engine) -> str:
    character_id = new_id()

    def create(connection: Connection) -> None:
        character_repository.insert(connection, character_id=character_id, card=CARD, now=STAMP)

    write(engine, create)
    return character_id


def add_message(engine: Engine, conversation_id: str, author: Author, content: str) -> str:
    """Stores a complete message with its one active variant and returns its id."""

    def work(connection: Connection) -> str:
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
        return message_id

    return write(engine, work)


def event(dimension: str, direction: str, magnitude: str, reason: str, *seqs: int) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "direction": direction,
        "magnitude": magnitude,
        "reason": reason,
        "source_seqs": list(seqs),
    }


def episode(importance: int, *seqs: int) -> dict[str, Any]:
    return {
        "title": "Una giornata difficile",
        "description": "Racconta la giornata al lavoro e si sente capita.",
        "participants": ["Utente", "Dario"],
        "importance": importance,
        "source_seqs": list(seqs),
        "linked_episode_refs": [],
    }


def output(*, events: Sequence[dict[str, Any]] = (), episodes: Sequence[dict[str, Any]] = (), **state: Any) -> str:
    update: dict[str, Any] = {
        "emotional_state": None,
        "current_topic": None,
        "open_threads_add": [],
        "open_threads_close": [],
        "goals": [],
        "narrative_change": None,
    }
    update.update(state)
    result = {
        "schema_version": 1,
        "memories": [],
        "episodes": list(episodes),
        "relationship_events": list(events),
        "state": update,
    }
    return json.dumps(result, ensure_ascii=False)


def rows(engine: Engine, sql: str, **params: object) -> list[dict[str, Any]]:
    return read(engine, lambda connection: [dict(row) for row in connection.execute(text(sql), params).mappings()])


class Chat:
    """A chat of `character_id` with the ten messages of CHAT, whose extraction windows run through the handler one
    after the other, each with its scripted output."""

    def __init__(self, engine: Engine, character_id: str) -> None:
        self.engine = engine
        self.conversation_id = ConversationService(engine).create(character_id).id
        self.message_ids = [add_message(engine, self.conversation_id, author, line) for author, line in CHAT]
        queue = JobQueue(clock=lambda: START)

        def due(connection: Connection) -> Job | None:
            return queue_extraction_if_due(connection, queue, self.conversation_id, ExtractionSettings())

        job = write(engine, due)
        assert job is not None, "ten messages make the chat due"
        self.job = job
        self.runtime = ScriptedRuntime()
        self.service = MemoryExtractionService(engine, self.runtime, queue=queue, clock=lambda: START, zone=ROME)

    async def extract(self, answer: str, *turns: tuple[Author, str]) -> dict[str, Any]:
        """Adds `turns` as new messages, runs the next window with `answer` and returns the completed job's result."""
        for author, line in turns:
            self.message_ids.append(add_message(self.engine, self.conversation_id, author, line))
        self.runtime.outputs.append(answer)
        done = await self.service(self.job, RunContext(lease=None, genkey="genkey"))
        assert isinstance(done, JobDone)
        assert done.result is not None
        assert done.result["status"] == "completed"
        return dict(done.result)

    def history(self) -> list[dict[str, Any]]:
        sql = "SELECT * FROM character_state_history WHERE conversation_id = :id ORDER BY created_at, rowid"
        return rows(self.engine, sql, id=self.conversation_id)

    def state_row(self) -> dict[str, Any] | None:
        sql = "SELECT * FROM relationship_states WHERE conversation_id = :id"
        found = rows(self.engine, sql, id=self.conversation_id)
        return found[0] if found else None

    def block_four(self) -> StateText:
        def load(connection: Connection) -> StateText:
            request = load_context_request(connection, self.conversation_id, 10_000, user=UserProfile(), now=START)
            assert request is not None
            return request.state

        return read(self.engine, load)

    def replayed(self) -> RelationshipState:
        def load(connection: Connection) -> RelationshipState:
            return replay(state_repository.applied_events(connection, self.conversation_id), ROME).state

        return read(self.engine, load)


@pytest.mark.anyio
async def test_a_run_stores_bounded_rows_with_reasons_and_sources(migrated: Engine) -> None:
    """T-CON-06, extraction half: the rows of a run, what they asked and got, and the state they fold to."""
    chat = Chat(migrated, new_character(migrated))
    answer = output(
        episodes=[episode(4, 3, 5)],
        events=[
            event("trust", "up", "large", "Si confida sul lavoro.", 5, 3, 5),
            event("trust", "up", "small", "Ride con lui.", 6),
            event("tension", "down", "small", "Si scusa per ieri.", 7),
            event("familiarity", "up", "medium", "Parlano a lungo.", 1, 99),
        ],
        emotional_state="È stanca ma serena",
        current_topic="il lavoro",
        open_threads_add=[THREAD],
        goals=["Vuole dormire di più."],
    )

    result = await chat.extract(answer)

    assert (result["state_events"], result["dropped_items"], result["deferred_items"]) == (6, 1, 1)
    history = chat.history()
    window = chat.message_ids
    assert [
        (row["event_kind"], row["dimension"], row["delta"], row["value_before"], row["value_after"]) for row in history
    ] == [
        ("relationship_change", "trust", 8, "20", "28"),
        ("relationship_change", "trust", 2, "28", "28"),
        ("relationship_change", "tension", -2, "0", "0"),
        ("emotion_change", None, None, None, "È stanca ma serena"),
        ("thread_opened", None, None, None, THREAD),
        ("goal_change", None, None, None, "Vuole dormire di più."),
    ]
    assert [json.loads(row["source_message_ids_json"]) for row in history] == [
        [window[2], window[4]],
        [window[5]],
        [window[6]],
        window,
        window,
        window,
    ]
    assert [row["reason"] for row in history] == [
        "Si confida sul lavoro.",
        "Ride con lui.",
        "Si scusa per ieri.",
        UPDATE_REASONS["emotion_change"],
        UPDATE_REASONS["thread_opened"],
        UPDATE_REASONS["goal_change"],
    ]
    [run] = rows(migrated, "SELECT id FROM memory_extraction_runs WHERE conversation_id = :id", id=chat.conversation_id)
    assert {(row["run_id"], row["status"], row["created_at"]) for row in history} == {(run["id"], "applied", STAMP)}

    state = chat.state_row()
    assert state is not None
    episodes = rows(migrated, "SELECT id FROM episodic_memories WHERE conversation_id = :id", id=chat.conversation_id)
    assert (state["familiarity"], state["trust"], state["confidence"], state["tension"]) == (10, 28, 10, 0)
    assert (state["emotional_state"], state["current_topic"], state["last_history_id"]) == (
        "È stanca ma serena",
        None,
        history[-1]["id"],
    )
    assert json.loads(state["open_threads_json"]) == [THREAD]
    assert json.loads(state["goals_json"]) == ["Vuole dormire di più."]
    assert json.loads(state["recent_event_ids_json"]) == [episodes[0]["id"]]
    replayed = chat.replayed()
    assert (replayed.trust, replayed.emotional_state, replayed.open_threads) == (28, "È stanca ma serena", (THREAD,))

    # Without an episode of importance 4 a large change counts as medium.
    other = Chat(migrated, new_character(migrated))
    await other.extract(output(episodes=[episode(3, 1)], events=[event("trust", "up", "large", "Si fida.", 5)]))
    [row] = other.history()
    assert (row["delta"], row["value_before"], row["value_after"]) == (4, "20", "24")


@pytest.mark.anyio
async def test_a_state_change_in_one_chat_leaves_another_chat_unchanged(migrated: Engine) -> None:
    """T-CON-03: two chats of one character."""
    character_id = new_character(migrated)
    first, second = Chat(migrated, character_id), Chat(migrated, character_id)
    assert first.block_four() == StateText(), "no block 4 before a run writes the state"
    await second.extract(output(events=[event("trust", "up", "small", "Lo ringrazia.", 5)]))
    second_rows = second.history()
    second_state = second.state_row()
    second_block = second.block_four()
    assert second_block == StateText((BANDS_AT_START,), ())

    opening = output(
        episodes=[episode(4, 3)],
        events=[
            event("trust", "up", "large", "Si confida.", 5),
            event("confidence", "up", "medium", "Racconta.", 3),
        ],
        emotional_state="È stanca ma serena",
        open_threads_add=[THREAD],
    )
    await first.extract(opening)
    closing = output(
        events=[event("trust", "down", "small", "Un malinteso.", 11)],
        open_threads_close=[THREAD.rstrip(".")],
    )
    await first.extract(
        closing,
        (Author.USER, "Con Marta è andata benissimo, alla fine."),
        (Author.CHARACTER, "Che bella notizia!"),
    )

    assert f"Discorsi in sospeso:\n- {THREAD}\n" in first.runtime.requests[-1].prompt, "the prompt shows open threads"
    shown_to_second = second.runtime.requests[-1].prompt.rsplit("Discorsi in sospeso:\n", 1)[1]
    assert shown_to_second.startswith("nessuno\n"), "the other chat's threads never show"
    state = first.state_row()
    assert state is not None
    assert (state["trust"], state["confidence"], json.loads(state["open_threads_json"])) == (26, 14, [])
    assert first.block_four() == StateText((BANDS_AT_START, "Come ti senti ora: È stanca ma serena."), ())

    assert second.history() == second_rows
    assert second.state_row() == second_state
    assert second.block_four() == second_block
    manager = CharacterStateManager(ROME)

    def invalidate_across(connection: Connection) -> tuple[int, int]:
        return (
            manager.invalidate(connection, second.conversation_id, first.message_ids, START),
            manager.invalidate(connection, first.conversation_id, second.message_ids, START),
        )

    assert write(migrated, invalidate_across) == (0, 0), "a chat's rows never cite another chat's messages"
    assert second.state_row() == second_state


# One change the T-MEM-23 test asked for: its run, dimension, requested delta, local day and source messages.
Asked = tuple[str, str, int, date, frozenset[str]]


def _oracle(changes: Sequence[Asked]) -> dict[str, int]:
    """An independent reading of the update rules: one point at a time, up to the range and the run's and the day's
    limits."""
    values = dict(INITIAL_VALUES)
    by_run: dict[tuple[str, str], int] = defaultdict(int)
    by_day: dict[tuple[date, str], int] = defaultdict(int)
    for run, dimension, delta, day, _ in changes:
        step = 1 if delta > 0 else -1
        for _ in range(abs(delta)):
            value = values[dimension] + step
            if not 0 <= value <= 100 or abs(by_run[(run, dimension)] + step) > RUN_LIMIT:
                break
            if abs(by_day[(day, dimension)] + step) > DAY_LIMIT:
                break
            values[dimension] = value
            by_run[(run, dimension)] += step
            by_day[(day, dimension)] += step
    return values


def _invalidate(
    engine: Engine, manager: CharacterStateManager, conversation_id: str, message: str, at: datetime
) -> int:
    return write(engine, lambda connection: manager.invalidate(connection, conversation_id, [message], at))


def _apply_run(
    engine: Engine,
    manager: CharacterStateManager,
    run: StateRun,
    events: Sequence[tuple[RelationshipEvent, Sequence[str]]],
    update: StateUpdate,
) -> None:
    def work(connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO memory_extraction_runs (id, conversation_id, from_seq, to_seq, window_hash, "
                "prompt_version, schema_version, status, created_at) VALUES (:id, :conversation, 1, 1, :id, 'test', "
                "'1', 'completed', :now)"
            ),
            {"id": run.run_id, "conversation": run.conversation_id, "now": STAMP},
        )
        manager.apply_run(connection, run, events, update)

    write(engine, work)


def _stored_and_replayed(engine: Engine, conversation_id: str) -> tuple[StoredState | None, RelationshipState]:
    def load(connection: Connection) -> tuple[StoredState | None, RelationshipState]:
        stored = state_repository.load_state(connection, conversation_id)
        return stored, replay(state_repository.applied_events(connection, conversation_id), ROME).state

    return read(engine, load)


def _random_run(
    rng: random.Random,
    conversation_id: str,
    character_id: str,
    messages: Sequence[str],
    cited: str | None,
    at: datetime,
) -> tuple[StateRun, list[tuple[RelationshipEvent, list[str]]], StateUpdate, list[Asked]]:
    """Returns a random run with up to four relationship events and a random state update. `cited` is a message every
    event and the window cite, as a re-evaluation after an edit does. The run allows no large change, so `large` asks
    for 4."""
    run_id = new_id()
    extra = [] if cited is None else [cited]
    window = sorted({rng.choice(messages) for _ in range(3)} | set(extra))
    events: list[tuple[RelationshipEvent, list[str]]] = []
    asked: list[Asked] = []
    for _ in range(rng.randint(0, 4)):
        sources = sorted({rng.choice(window), *extra})
        proposed = RelationshipEvent.model_validate(
            {
                "dimension": rng.choice(("familiarity", "trust", "confidence", "tension")),
                "direction": rng.choice(("up", "down")),
                "magnitude": rng.choice(("small", "medium", "large")),
                "reason": "Un motivo.",
                "source_seqs": [1],
            }
        )
        events.append((proposed, sources))
        size = 2 if proposed.magnitude == "small" else 4
        delta = size if proposed.direction == "up" else -size
        asked.append((run_id, proposed.dimension, delta, at.astimezone(ROME).date(), frozenset(sources)))
    threads = ("Il colloquio.", "Il libro di Marta.", "La cena di sabato.")
    update = StateUpdate(
        emotional_state=rng.choice((None, "Allegra", "Pensierosa")),
        open_threads_add=rng.sample(threads, rng.randint(0, 2)),
        open_threads_close=rng.sample(threads, rng.randint(0, 1)),
    )
    run = StateRun(run_id, conversation_id, character_id, "it", tuple(window), False, at)
    return run, events, update, asked


def test_random_invalidations_keep_the_stored_state_equal_to_a_replay(migrated: Engine) -> None:
    """T-MEM-23, database half: runs, edits (invalidation, then a run citing the message again) and deletions
    (invalidation) through CharacterStateManager. After each step the stored row equals the replay of the applied rows
    read back, and its values equal an independent reading of the rules over the changes the test asked for and did
    not invalidate."""
    manager = CharacterStateManager(ROME)
    character_id = new_character(migrated)
    for seed in range(6):
        rng = random.Random(seed)
        conversation_id = ConversationService(migrated).create(character_id).id
        messages = [add_message(migrated, conversation_id, Author.USER, f"Messaggio {n}.") for n in range(8)]
        asked: list[Asked] = []
        invalidated: set[int] = set()
        moment = START
        for step in range(20):
            moment += timedelta(minutes=rng.choice((0, 45, 300, 900)))
            roll = rng.random()
            message = rng.choice(messages)
            if roll < 0.3:
                invalidated |= {index for index, item in enumerate(asked) if message in item[4]}
                _invalidate(migrated, manager, conversation_id, message, moment)
            if roll < 0.15 or roll >= 0.3:
                cited = message if roll < 0.15 else None
                run, events, update, more = _random_run(rng, conversation_id, character_id, messages, cited, moment)
                _apply_run(migrated, manager, run, events, update)
                asked += more

            stored, replayed = _stored_and_replayed(migrated, conversation_id)
            if stored is None:
                assert not asked, "a run that wrote a row also wrote the state"
                continue
            assert stored.state == replayed, f"seed {seed}, step {step}"
            surviving = [item for index, item in enumerate(asked) if index not in invalidated]
            assert {name: stored.state.value(name) for name in INITIAL_VALUES} == _oracle(surviving), (seed, step)
