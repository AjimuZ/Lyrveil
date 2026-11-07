"""MemoryExtractionService: the `memory_extraction` job, which turns a window of messages into conversation memories and
episodes with their sources, and the trigger that queues it (docs/memory-engine.md, Extraction pipeline).

A run takes the window after the conversation's cursor, looks up the run of that window under the current prompt and
schema version, and stops there when it is already completed. Otherwise it generates `ExtractionResult` under the
grammar of its schema with the loaded text model, validates it and repairs it once (lyrveil.prompts.structured). Then
one transaction applies the consistency rules to each memory item (MemoryConsistencyService) and writes the memories,
their sources, revisions and index rows, the episodes, the run and the new cursor. Invalid output, a runtime failure
or an interruption changes no memory, no message and no cursor; the job retries with the backoff of its kind.

The relationship events and state of the Character State Manager (P04.6) are validated and counted, not applied. An
item with an unknown E/P label or a source outside the window is dropped and counted. No message, prompt, memory or
model output is ever logged; `raw_output` stays in the run row.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from anyio import to_thread
from pydantic import ValidationError
from sqlalchemy import Connection, Engine

from lyrveil.catalog import TextTemplate
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.conversations import ConversationStatus
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.memory_extraction import (
    SCHEMA_VERSION,
    TARGETED_RELATIONS,
    ExtractedEpisode,
    ExtractedMemory,
    ExtractionResult,
)
from lyrveil.domain.messages import Author
from lyrveil.events.hub import EventHub
from lyrveil.jobs.handlers import INTERNAL, JobDone, JobFailedError, JobOutcome, RunContext
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import Job, JobQueue, QueueFullError
from lyrveil.jobs.scheduler import write
from lyrveil.logs import get_logger
from lyrveil.memory.text import fingerprint, match_query
from lyrveil.memory.window import (
    CONTEXT_MESSAGES,
    ExtractionSettings,
    Window,
    WindowMessage,
    is_due,
    select_window,
    significant_patterns,
)
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import memory_extraction as repository
from lyrveil.persistence.memory_index import index_episode
from lyrveil.ports.text_runtime import TextRuntime, TextRuntimeError
from lyrveil.prompts import extraction
from lyrveil.prompts.extraction import ExtractionPrompt, Labeled, PromptMessage
from lyrveil.prompts.gbnf import schema_to_gbnf
from lyrveil.prompts.structured import (
    STRUCTURED_OUTPUT_INVALID,
    OutputRejectedError,
    StructuredOutputInvalidError,
    StructuredResult,
    generate_structured,
)
from lyrveil.services.memory_consistency import MemoryConsistencyService, RunScope

# Generated once from the schema, so the grammar always follows the model.
EXTRACTION_GRAMMAR = schema_to_gbnf(ExtractionResult)
PROMPT_VERSION = extraction.TEMPLATE_VERSION
# The existing memories and recent episodes a prompt shows as E1..En and P1..Pn.
MAX_EXISTING_MEMORIES = 12
MAX_RECENT_EPISODES = 5
# Distinct stems of the window that look up related memories, so a long window still gives a bounded query.
MAX_QUERY_TERMS = 64
# Messages read after the cursor to find a window or decide a trigger: the 12 of a window and room for passed-over ones.
SCAN_LIMIT = 200
MAX_PARTICIPANTS = 12
MAX_PARTICIPANT_CHARS = 80
EPISODE_LINK = "related"
_NOT_A_RESULT = "La risposta non è un risultato di estrazione valido."

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _describe(error: ValidationError) -> str:
    """Returns the Italian detail of the first problem pydantic found. It names the field, never the value, because it
    reaches the repair prompt and the job's `error_detail`."""
    first = error.errors()[0]
    path = ".".join(str(part) for part in first["loc"])
    if not path:
        return _NOT_A_RESULT
    if first["type"] == "missing":
        return f"Manca il campo «{path}»."
    if first["type"] == "extra_forbidden":
        return f"Il campo «{path}» non è previsto."
    return f"Il campo «{path}» non è valido."


def validate_result(raw: dict[str, Any]) -> ExtractionResult:
    """Returns the parsed output as an ExtractionResult. Raises OutputRejectedError with an Italian detail when it does
    not match the schema. References and sources are checked later, item by item: a bad one drops its item, not the
    whole output."""
    try:
        return ExtractionResult.model_validate(raw)
    except ValidationError as error:
        raise OutputRejectedError(_describe(error)) from None


def queue_extraction_if_due(
    connection: Connection,
    queue: JobQueue,
    conversation_id: str,
    settings: ExtractionSettings,
    *,
    exclude_job_id: str | None = None,
) -> Job | None:
    """Queues a `memory_extraction` job for the conversation in the caller's write transaction when it is due
    (lyrveil.memory.window.is_due), and returns it; otherwise returns None and writes nothing.

    Never queues for a missing or deleted conversation, nor while another extraction job of the conversation is queued
    or running (`exclude_job_id` is the run that asks, which does not count). A full queue queues nothing and raises
    nothing: the next trigger tries again.
    """
    conversation = conversation_repository.get(connection, conversation_id)
    if conversation is None or conversation.status is ConversationStatus.DELETED:
        return None
    if repository.has_active_extraction_job(connection, conversation_id, exclude_job_id):
        return None
    cursor = repository.cursor(connection, conversation_id)
    after = repository.messages_after(connection, conversation_id, cursor, limit=SCAN_LIMIT)
    if not is_due(after, settings.every_messages, significant_patterns(conversation.effective_language)):
        return None
    try:
        return queue.enqueue(
            connection,
            JobKind.MEMORY_EXTRACTION,
            {},
            conversation_id=conversation_id,
            character_id=conversation.character_id,
        )
    except QueueFullError:
        logger.info("memory_extraction_not_queued", conversation_id=conversation_id, state="queue_full")
        return None


@dataclass(frozen=True)
class _Skipped:
    """A run that needs no generation. `reason` is `conversation_deleted`, `nothing_to_extract` or
    `already_extracted`."""

    reason: str
    run_id: str | None = None


@dataclass(frozen=True)
class _Prepared:
    """A started run: its window, what its prompt shows, and the labels the model may use."""

    run_id: str
    run_attempt: int
    conversation_id: str
    character_id: str
    language: str
    window: Window
    prompt: ExtractionPrompt
    memory_refs: Mapping[str, repository.ShownMemory]
    episode_refs: Mapping[str, repository.ShownEpisode]


@dataclass
class _Counts:
    """What a run did with the model's items; stored in the job's result and logged. `conflict_ids` are the new
    uncertain memories that fire `memory.conflict`."""

    memories_added: int = 0
    memories_confirmed: int = 0
    memories_superseded: int = 0
    memories_uncertain: int = 0
    episodes_added: int = 0
    episodes_confirmed: int = 0
    sources_added: int = 0
    dropped_items: int = 0
    deferred_items: int = 0
    memory_ids: list[str] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)


class MemoryExtractionService:
    """Runs one extraction per call, as the JobHandler of `memory_extraction` (lyrveil.jobs.handlers).

    `runtime` must be used only under the run's lease, which the scheduler holds. `template` is the prompt family of
    the loaded text model and `user` names the user in the prompt. `settings` gives the trigger threshold that a
    finished run checks again for the messages after its window. `events`, when given, receives `memory.conflict` for
    each conflict a committed run stored. `clock` returns the aware current time; tests pass a fake one.
    """

    def __init__(
        self,
        engine: Engine,
        runtime: TextRuntime | None = None,
        *,
        template: TextTemplate = "gemma4",
        user: UserProfile | None = None,
        settings: ExtractionSettings | None = None,
        queue: JobQueue | None = None,
        events: EventHub | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._template = template
        self._user = user or UserProfile()
        self._settings = settings or ExtractionSettings()
        self._queue = queue or JobQueue(clock=clock)
        self._events = events
        self._consistency = MemoryConsistencyService()
        self._clock = clock

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        """Analyzes the next window of the job's conversation and returns JobDone with `status` and what it stored.

        `status` is `completed` (with `run_id`, `run_attempt`, `from_seq`, `to_seq`, `attempts` and the counts of
        _Counts), `already_extracted` (the window's run was completed before: no generation, the cursor moves past
        it), `nothing_to_extract`, `conversation_deleted`, or `superseded` (the window's messages changed while the
        model ran: nothing is written and the trigger is checked again). After a completed run, publishes
        `memory.conflict` with `memory_id` and `conversation_id` for each id of `conflict_ids`. Raises JobFailedError
        STRUCTURED_OUTPUT_INVALID, retryable, when the output and its repair are both refused; a TextRuntimeError goes
        through for the scheduler to retry. Either way the run is recorded `failed` and nothing else changes. Raises
        INTERNAL for a job without a conversation.
        """
        if self._runtime is None:
            raise RuntimeError("the extraction handler needs a text runtime")
        if job.conversation_id is None:
            raise JobFailedError(INTERNAL, None, retryable=False)
        prepared = await to_thread.run_sync(self._prepare, job, job.conversation_id)
        if isinstance(prepared, _Skipped):
            logger.info(
                "memory_extraction_skipped", job_id=job.id, conversation_id=job.conversation_id, state=prepared.reason
            )
            return JobDone({"status": prepared.reason, "run_id": prepared.run_id})
        shown = prepared.prompt
        try:
            result = await generate_structured(
                self._runtime,
                prompt=lambda repair: extraction.render_prompt(self._template, shown, repair),
                grammar=EXTRACTION_GRAMMAR,
                validate=validate_result,
                genkey=context.genkey,
                max_length=extraction.MAX_LENGTH,
                temperature=extraction.TEMPERATURE,
            )
        except StructuredOutputInvalidError as invalid:
            await to_thread.run_sync(self._fail_run, prepared.run_id, STRUCTURED_OUTPUT_INVALID, invalid.output)
            logger.info(
                "memory_extraction_failed",
                job_id=job.id,
                conversation_id=prepared.conversation_id,
                error_code=STRUCTURED_OUTPUT_INVALID,
            )
            raise JobFailedError(STRUCTURED_OUTPUT_INVALID, invalid.detail, retryable=True) from None
        except TextRuntimeError as error:
            await to_thread.run_sync(self._fail_run, prepared.run_id, error.code, None)
            raise
        outcome = await to_thread.run_sync(self._commit, job, prepared, result)
        logger.info(
            "memory_extraction_finished",
            job_id=job.id,
            conversation_id=prepared.conversation_id,
            state=str(outcome["status"]),
            memory_count=int(outcome.get("memories_added", 0)),
            superseded_count=int(outcome.get("memories_superseded", 0)),
            conflict_count=len(outcome.get("conflict_ids", [])),
            dropped_count=int(outcome.get("dropped_items", 0)),
            deferred_count=int(outcome.get("deferred_items", 0)),
            attempt_count=result.attempts,
        )
        if self._events is not None:
            for memory_id in outcome.get("conflict_ids", []):
                self._events.publish(
                    "memory.conflict", {"memory_id": memory_id, "conversation_id": prepared.conversation_id}
                )
        return JobDone(outcome)

    def _prepare(self, job: Job, conversation_id: str) -> _Prepared | _Skipped:
        """Selects the window and starts its run in one transaction, or tells why no generation is needed. Temporary
        memories past their expiry become `expired` first, so the prompt never shows them. A window whose run was
        completed moves the cursor past it here, and the trigger is checked for what follows."""
        moment = self._clock()
        now = to_utc_text(moment)

        def work(connection: Connection) -> _Prepared | _Skipped:
            conversation = conversation_repository.get(connection, conversation_id)
            if conversation is None or conversation.status is ConversationStatus.DELETED:
                return _Skipped("conversation_deleted")
            self._consistency.expire(connection, conversation_id, moment)
            cursor = repository.cursor(connection, conversation_id)
            after = repository.messages_after(connection, conversation_id, cursor, limit=SCAN_LIMIT)
            window = select_window(after, cursor)
            if window is None:
                return _Skipped("nothing_to_extract")
            run = repository.find_run(connection, conversation_id, window.hash, PROMPT_VERSION, SCHEMA_VERSION)
            if run is not None and run.status == "completed":
                repository.advance_cursor(connection, conversation_id, window.to_seq)
                queue_extraction_if_due(connection, self._queue, conversation_id, self._settings, exclude_job_id=job.id)
                return _Skipped("already_extracted", run.id)
            if run is None:
                run_id, run_attempt = new_id(), 1
                repository.insert_run(
                    connection,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    window=window,
                    prompt_version=PROMPT_VERSION,
                    schema_version=SCHEMA_VERSION,
                    now=now,
                )
            else:
                run_id, run_attempt = run.id, run.attempt + 1
                repository.restart_run(connection, run_id, window)
            character = conversation_repository.find_profile(connection, conversation.character_id)
            assert character is not None, "a conversation's character cannot be removed while the chat exists"
            language = conversation.effective_language
            existing = _existing(connection, conversation_id, window, language)
            episodes = repository.recent_episodes(connection, conversation_id, MAX_RECENT_EPISODES)
            context = repository.context_before(connection, conversation_id, window.from_seq, CONTEXT_MESSAGES)
            memory_refs = {f"E{number}": memory for number, memory in enumerate(existing, 1)}
            episode_refs = {f"P{number}": episode for number, episode in enumerate(episodes, 1)}
            names = {Author.USER: self._user.display_name, Author.CHARACTER: character.first_name}
            prompt = ExtractionPrompt(
                user_name=self._user.display_name,
                character_name=character.full_name,
                language=language,
                existing=[
                    Labeled(ref, memory.content, uncertain=memory.status == "uncertain")
                    for ref, memory in memory_refs.items()
                ],
                episodes=[Labeled(ref, episode.title) for ref, episode in episode_refs.items()],
                context=_prompt_messages(context, names),
                window=_prompt_messages(window.messages, names),
            )
            return _Prepared(
                run_id=run_id,
                run_attempt=run_attempt,
                conversation_id=conversation_id,
                character_id=conversation.character_id,
                language=language,
                window=window,
                prompt=prompt,
                memory_refs=memory_refs,
                episode_refs=episode_refs,
            )

        return write(self._engine, work)

    def _fail_run(self, run_id: str, error_code: str, raw_output: str | None) -> None:
        """Records the run `failed` with the error code and the refused output; nothing else is written."""
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> None:
            repository.finish_run(connection, run_id, "failed", raw_output=raw_output, error_code=error_code, now=now)

        write(self._engine, work)

    def _commit(self, job: Job, prepared: _Prepared, result: StructuredResult[ExtractionResult]) -> dict[str, Any]:
        """Writes the run's items, the completed run and the cursor in one transaction and returns the job result.

        The window is read again first: if the cursor moved or a message of the window changed while the model ran,
        the run becomes `superseded`, nothing else is written, and the trigger is checked again for the current
        messages.
        """
        moment = self._clock()
        now = to_utc_text(moment)
        window = prepared.window
        conversation_id = prepared.conversation_id
        base: dict[str, Any] = {
            "run_id": prepared.run_id,
            "run_attempt": prepared.run_attempt,
            "from_seq": window.from_seq,
            "to_seq": window.to_seq,
        }

        def work(connection: Connection) -> dict[str, Any]:
            cursor = repository.cursor(connection, conversation_id)
            again = repository.messages_after(
                connection, conversation_id, window.from_seq - 1, limit=SCAN_LIMIT, up_to_seq=window.to_seq
            )
            current = select_window(again, window.from_seq - 1)
            if cursor != window.from_seq - 1 or current is None or current.hash != window.hash:
                repository.finish_run(
                    connection, prepared.run_id, "superseded", raw_output=result.text, error_code=None, now=now
                )
                queue_extraction_if_due(connection, self._queue, conversation_id, self._settings, exclude_job_id=job.id)
                return {"status": "superseded", **base}
            counts = self._apply(connection, prepared, result.value, moment)
            repository.finish_run(
                connection, prepared.run_id, "completed", raw_output=result.text, error_code=None, now=now
            )
            repository.advance_cursor(connection, conversation_id, window.to_seq)
            queue_extraction_if_due(connection, self._queue, conversation_id, self._settings, exclude_job_id=job.id)
            return {
                "status": "completed",
                **base,
                "attempts": result.attempts,
                "memories_added": counts.memories_added,
                "memories_confirmed": counts.memories_confirmed,
                "memories_superseded": counts.memories_superseded,
                "memories_uncertain": counts.memories_uncertain,
                "episodes_added": counts.episodes_added,
                "episodes_confirmed": counts.episodes_confirmed,
                "sources_added": counts.sources_added,
                "dropped_items": counts.dropped_items,
                "deferred_items": counts.deferred_items,
                "memory_ids": counts.memory_ids,
                "conflict_ids": counts.conflict_ids,
            }

        return write(self._engine, work)

    def _apply(self, connection: Connection, prepared: _Prepared, result: ExtractionResult, now: datetime) -> _Counts:
        """Applies the items of `result` that the pipeline stores and counts the rest. Requires the caller's write
        transaction."""
        by_seq = {message.seq: message for message in prepared.window.messages}
        scope = RunScope(
            run_id=prepared.run_id,
            character_id=prepared.character_id,
            conversation_id=prepared.conversation_id,
            language=prepared.language,
            now=now,
        )
        counts = _Counts()
        for item in result.memories:
            self._apply_memory(connection, prepared, scope, item, by_seq, counts)
        for episode in result.episodes:
            _apply_episode(connection, prepared, episode, by_seq, now, counts)
        for event in result.relationship_events:
            # The Character State Manager (P04.6) applies relationship events; a valid one is only counted until then.
            if _sources(event.source_seqs, by_seq) is None:
                counts.dropped_items += 1
            else:
                counts.deferred_items += 1
        return counts

    def _apply_memory(
        self,
        connection: Connection,
        prepared: _Prepared,
        scope: RunScope,
        item: ExtractedMemory,
        by_seq: Mapping[int, WindowMessage],
        counts: _Counts,
    ) -> None:
        """Drops an item with a source outside the window, an unknown label, a missing target or blank text, and
        hands the others to MemoryConsistencyService, which decides what is stored."""
        sources = _sources(item.source_seqs, by_seq)
        target = None if item.target_ref is None else prepared.memory_refs.get(item.target_ref)
        unknown_target = item.target_ref is not None and target is None
        missing_target = item.relation in TARGETED_RELATIONS and target is None
        if sources is None or unknown_target or missing_target or not item.content.strip():
            counts.dropped_items += 1
            return
        applied = self._consistency.apply(connection, scope, item, None if target is None else target.id, sources)
        if applied is None:
            counts.dropped_items += 1
            return
        counts.sources_added += applied.sources_added
        counts.memories_superseded += applied.superseded
        counts.memories_uncertain += applied.uncertain
        if not applied.added:
            counts.memories_confirmed += 1
            return
        counts.memories_added += 1
        counts.memory_ids.append(applied.memory_id)
        if applied.conflict:
            counts.conflict_ids.append(applied.memory_id)


def _prompt_messages(messages: Sequence[WindowMessage], names: Mapping[Author, str]) -> list[PromptMessage]:
    return [PromptMessage(m.seq, names.get(m.author, m.author.value), m.content) for m in messages]


def _existing(
    connection: Connection, conversation_id: str, window: Window, language: str
) -> list[repository.ShownMemory]:
    """Returns the memories the prompt shows: those whose words the window shares, best match first, then the most
    recently changed ones, up to MAX_EXISTING_MEMORIES."""
    match = match_query(" ".join(m.content for m in window.messages), language, max_terms=MAX_QUERY_TERMS)
    shown: list[repository.ShownMemory] = []
    if match is not None:
        shown = repository.related_memories(connection, conversation_id, match, MAX_EXISTING_MEMORIES)
    if len(shown) < MAX_EXISTING_MEMORIES:
        known = {memory.id for memory in shown}
        recent = repository.recent_memories(connection, conversation_id, MAX_EXISTING_MEMORIES)
        shown += [memory for memory in recent if memory.id not in known][: MAX_EXISTING_MEMORIES - len(shown)]
    return shown


def _sources(seqs: Sequence[int], by_seq: Mapping[int, WindowMessage]) -> list[WindowMessage] | None:
    """Returns the window messages of `seqs`, each once and in seq order, or None when one is outside the window."""
    unique = sorted(set(seqs))
    if any(seq not in by_seq for seq in unique):
        return None
    return [by_seq[seq] for seq in unique]


def _apply_episode(
    connection: Connection,
    prepared: _Prepared,
    item: ExtractedEpisode,
    by_seq: Mapping[int, WindowMessage],
    now: datetime,
    counts: _Counts,
) -> None:
    sources = _sources(item.source_seqs, by_seq)
    linked = [prepared.episode_refs.get(ref) for ref in dict.fromkeys(item.linked_episode_refs)]
    title, description = item.title.strip(), item.description.strip()
    if sources is None or any(episode is None for episode in linked) or not title or not description:
        counts.dropped_items += 1
        return
    episode_print = fingerprint(f"{title} {description}", prepared.language)
    same = repository.find_episode_by_fingerprint(connection, prepared.conversation_id, episode_print)
    if same is not None:
        counts.sources_added += repository.add_episode_sources(connection, same.id, sources)
        counts.episodes_confirmed += 1
        return
    participants = [name.strip()[:MAX_PARTICIPANT_CHARS] for name in item.participants if name.strip()]
    episode = repository.NewEpisode(
        id=new_id(),
        character_id=prepared.character_id,
        conversation_id=prepared.conversation_id,
        title=title,
        description=description,
        occurred_at=max(source.created_at for source in sources),
        participants=participants[:MAX_PARTICIPANTS],
        importance=item.importance,
        fingerprint=episode_print,
    )
    repository.insert_episode(connection, episode, to_utc_text(now))
    counts.sources_added += repository.add_episode_sources(connection, episode.id, sources)
    index_episode(connection, episode.id, title, description, prepared.language)
    for other in linked:
        if other is not None:
            repository.link_episodes(connection, episode.id, other.id, EPISODE_LINK)
    counts.episodes_added += 1
