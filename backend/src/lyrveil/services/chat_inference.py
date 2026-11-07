"""ChatInferenceService: the handler of `chat_reply` and `chat_regenerate`, which streams a reply into its variant.

docs/backend.md (Streaming persistence, Job handlers). A run fills the `streaming` variant its job names and
never adds one, so a second run overwrites the same variant. Each token goes to the event hub as
`message.delta` as soon as the runtime yields it. The text is written to the variant and the message every
FLUSH_INTERVAL_S seconds and at the end, each time in its own short transaction, so no transaction stays open
while the reply streams. After a crash the reply keeps what was written: `recover_interrupted_replies` marks it
`interrupted` at the next start.

The prompt is built by lyrveil.context.builder.ContextBuilder (docs/memory-engine.md, Context composition) in the
family of `ReplySettings.template`, with the runtime's own token counts, the character's length band as `max_length`,
the conversation's memories and summaries, and the adaptive notes that the checks of the last replies started. Every
token passes through the V2 and V3 cut and the card's ReplyNormalizer (lyrveil.consistency.normalizer) in `_stream`,
and only the text the normalizer releases is published and stored, so the client, the database and later prompts all
see the normalized reply. Each released sentence is checked (lyrveil.consistency.checks): a V4 hit ends the reply
`failed` with a notice, and a V6, V10 or V11 hit stops the first generation, publishes `message.restarted` and
generates once more with the matching note in the anchor; an empty reply (V1) is generated once more too. No message
text, prompt or token is ever logged: the assembly is logged as counts, and the checks by their ids.

After a finished reply is stored and announced, the use of its memories is recorded and the memory extraction trigger
runs (lyrveil.services.memory_extraction.queue_extraction_if_due), each in its own transaction, so a failure there
never undoes the reply. A regenerated reply that extraction had already passed also queues its re-evaluation
(queue_reevaluation), since its regeneration invalidated what the earlier variant supported.
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import anyio
from anyio import to_thread
from sqlalchemy import Connection, Engine

from lyrveil.catalog import TextTemplate
from lyrveil.consistency.checks import (
    BLOCKING,
    NOTE_REPLIES,
    PREVIOUS_REPLIES,
    STOPPING,
    CheckRules,
    SentenceSplitter,
    TurnCutoff,
    active_notes,
    check_reply,
    first_stop,
    note_text,
    recorded_checks,
)
from lyrveil.consistency.normalizer import NormalizedReply, NormalizerRules, ReplyNormalizer
from lyrveil.context.blocks import MAX_NOTES, state_lines
from lyrveil.context.builder import (
    Assembly,
    ContextBuilder,
    ContextRequest,
    ContextSettings,
    StateText,
    SummaryText,
    split_messages,
)
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.conversations import ConversationStatus
from lyrveil.domain.errors import ContentPolicyBlockedError
from lyrveil.domain.messages import (
    PENDING,
    Author,
    Message,
    MessageStatus,
    VariantStatus,
    content_hash,
    message_document,
)
from lyrveil.jobs.handlers import (
    INTERNAL,
    LEASE_LOST,
    JobDone,
    JobFailedError,
    JobOutcome,
    JobPreemptedError,
    RunContext,
)
from lyrveil.jobs.queue import JOB_INTERRUPTED, Job, JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.memory.summaries import SummarySettings
from lyrveil.memory.window import ExtractionSettings
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import messages as repository
from lyrveil.persistence import relationship as state_repository
from lyrveil.persistence import scenarios as scenario_repository
from lyrveil.persistence import summaries as summary_repository
from lyrveil.ports.events import (
    MESSAGE_COMPLETED,
    MESSAGE_DELTA,
    MESSAGE_FAILED,
    MESSAGE_RESTARTED,
    MESSAGE_UPDATED,
    TYPING_STARTED,
    TYPING_STOPPED,
    EventPublisher,
)
from lyrveil.ports.text_runtime import (
    FinishReason,
    RuntimeStreamDroppedError,
    TextRequest,
    TextRuntime,
    TextRuntimeError,
)
from lyrveil.prompts.formatter import PromptFormatter, formatter_for
from lyrveil.services.memory_extraction import queue_extraction_if_due, queue_reevaluation
from lyrveil.services.memory_retrieval import FtsMemoryRetriever
from lyrveil.services.summaries import queue_summary_if_due

# docs/backend.md (Streaming persistence): the streamed text reaches the database at least this often.
FLUSH_INTERVAL_S = 2.0
# The newest messages a prompt reads before the token budget trims them; the summaries cover what lies before.
MAX_HISTORY_MESSAGES = 100
_NOTHING_TO_ANSWER = "Non c'è un messaggio a cui rispondere."
# The job error of a reply that came out empty (V1) twice, and of one that V4 blocked.
REPLY_EMPTY = "REPLY_EMPTY"
CONTENT_POLICY_BLOCKED = ContentPolicyBlockedError.code
# What a reply that V4 blocked keeps instead of its text: fixed, so it never quotes the reply.
BLOCKED_NOTICE = "Questa risposta è stata bloccata: accostava minori e contenuti sessuali."

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ReplySettings:
    """What a reply request takes besides the chat.

    P02 has no settings store yet, so the defaults are the documented ones: the `gemma4` family of the default text
    profiles of both tiers, the `gemma4` chat preset of docs/prompting.md (Sampler presets), `llm.max_length` 400 and
    the default user of docs/configuration.md. A sampler field left at None is not sent. `max_length` is the ceiling
    of every length band: the request sends the character's band, capped by it.
    """

    template: TextTemplate = "gemma4"
    user: UserProfile = field(default_factory=UserProfile)
    max_length: int = 400
    temperature: float | None = 1.0
    top_p: float | None = 0.95
    top_k: int | None = 64
    min_p: float | None = 0.0
    rep_pen: float | None = 1.0
    dry_multiplier: float | None = 0.8
    dry_base: float | None = 1.75
    dry_allowed_length: int | None = 2
    dry_penalty_last_n: int | None = 320
    dry_sequence_breakers: tuple[str, ...] | None = ("\n", ":", '"', "*")


@dataclass(frozen=True)
class _Reply:
    """The reply a run fills: the message, its variant, and the chat it belongs to."""

    message_id: str
    variant_id: str
    seq: int
    conversation_id: str
    character_id: str


@dataclass(frozen=True)
class _Generated:
    """A finished reply: its normalized text, the size of its last prompt, the memories and episodes its block 5
    held, and its `checks_json` (`_checks_record`)."""

    text: str
    status: MessageStatus
    prompt_tokens: int
    memory_ids: tuple[str, ...]
    checks: Mapping[str, object]


@dataclass
class _Pipeline:
    """What stands between the runtime and the client during one generation: the V2 and V3 cut, the card's
    normalizer, and the sentence splitter of the streaming checks."""

    cutoff: TurnCutoff
    normalizer: ReplyNormalizer
    sentences: SentenceSplitter


@dataclass(frozen=True)
class _Pass:
    """One generation streamed: `stopped` names the check that discarded it, and then `normalized` is None; otherwise
    `normalized` is the delivered text and `finish` how the generation ended."""

    normalized: NormalizedReply | None
    finish: FinishReason
    stopped: str | None = None


class _CheckFailedError(JobFailedError):
    """A reply check ended the run for good: `check` is V1 (empty twice) or V4 (blocked). `text` is what the reply
    keeps, "" or BLOCKED_NOTICE, and `checks` its `checks_json`. Never retried by the scheduler."""

    def __init__(self, code: str, check: str, text: str, checks: Mapping[str, object]) -> None:
        super().__init__(code, retryable=False)
        self.check = check
        self.text = text
        self.checks = checks


def _checks_record(normalized: Mapping[str, int], checks: Mapping[str, int], restarts: list[str]) -> dict[str, object]:
    """Returns a variant's `checks_json`: the normalizer's counts, the counts of the reply checks that fired on the
    delivered text (V2 and V3 included), the check that discarded each earlier generation, oldest first, and their
    number."""
    return {
        "normalizer": dict(normalized),
        "checks": dict(checks),
        "restarts": list(restarts),
        "regenerations": len(restarts),
    }


def _presence(reply: _Reply) -> dict[str, str]:
    """Returns the data of `typing.started` and `typing.stopped`."""
    return {"conversation_id": reply.conversation_id, "character_id": reply.character_id}


def _utf16_length(text: str) -> int:
    # Delta offsets count UTF-16 code units, the unit of JavaScript string indices in the browser client.
    return len(text.encode("utf-16-le")) // 2


def _store(
    connection: Connection,
    reply: _Reply,
    text: str,
    message_status: MessageStatus,
    variant_status: VariantStatus,
    checks: Mapping[str, object] | None = None,
) -> Message:
    """Writes `text` and the statuses into the reply's variant and message, and returns the message. `checks`, when
    given, becomes the variant's `checks_json`; otherwise that column is left as it is."""
    columns: dict[str, object] = {"content": text, "content_hash": content_hash(text), "status": variant_status.value}
    if checks is not None:
        columns["checks_json"] = json.dumps(checks, ensure_ascii=False)
    repository.update_variant(connection, reply.variant_id, **columns)
    repository.update_message(connection, reply.message_id, content=text, status=message_status.value)
    message = repository.get_message(connection, reply.message_id)
    assert message is not None
    return message


def _failure(error: Exception) -> tuple[str, bool]:
    """Returns the job error code of a failed run and whether the scheduler retries that kind of failure."""
    if isinstance(error, TextRuntimeError):
        return error.code, True
    if isinstance(error, JobFailedError):
        return error.code, error.retryable
    return INTERNAL, False


def load_context_request(
    connection: Connection,
    conversation_id: str,
    before_seq: int,
    *,
    user: UserProfile,
    now: datetime,
    previous_memory_ids: frozenset[str] = frozenset(),
) -> ContextRequest | None:
    """Reads what the reply at `before_seq` is built from: the character's whole card, the newest MAX_HISTORY_MESSAGES
    readable messages before it, the scenario copy the chat started with, the chat's active checkpoints, its state
    as block 4 (empty until a run has written one), and as `notes` the adaptive notes that the checks recorded on its
    last replies started (lyrveil.consistency.checks.active_notes).

    Everything is keyed by this conversation, so another chat of the same character adds nothing; memories are read
    later by the builder, under the same scope. The notes read only replies before `before_seq`, so a regenerated reply
    gets the notes its first variant had. Returns None when no user message has text. The conversation must exist.
    """
    conversation = conversation_repository.get(connection, conversation_id)
    assert conversation is not None
    stored = character_repository.get_card(connection, conversation.character_id)
    assert stored is not None, "a conversation's character cannot be removed while the chat exists"
    split = split_messages(repository.prompt_messages(connection, conversation_id, before_seq, MAX_HISTORY_MESSAGES))
    if split is None:
        return None
    history, current = split
    stored_state = state_repository.load_state(connection, conversation_id)
    state = StateText() if stored_state is None else StateText(*state_lines(stored_state.state, user))
    language = conversation.effective_language
    # The newest NOTE_REPLIES replies start notes, and the one before them tells whether a V7 hit was the second.
    recent = repository.reply_checks(connection, conversation_id, before_seq, NOTE_REPLIES + 1)
    started = active_notes([recorded_checks(checks) for checks in recent])
    notes = tuple(note_text(check, stored.card, user, language) for check in started)
    return ContextRequest(
        character_id=conversation.character_id,
        conversation_id=conversation_id,
        card=stored.card,
        user=user,
        language=language,
        shares_memory=conversation.shares_memory,
        history=history,
        current=current,
        summaries=[
            SummaryText(summary.checkpoint, summary.text)
            for summary in summary_repository.active_summaries(connection, conversation_id)
        ],
        now=now,
        scenario=scenario_repository.snapshot_of(connection, conversation_id),
        state=state,
        notes=notes,
        previous_memory_ids=previous_memory_ids,
    )


def _log_assembly(job_id: str, conversation_id: str, assembly: Assembly) -> None:
    """Logs what the prompt holds as counts only: block sizes, items and reductions, never text."""
    sizes = {f"{name}_tokens": tokens for name, tokens in assembly.blocks.items()}
    logger.info(
        "context_assembled",
        job_id=job_id,
        conversation_id=conversation_id,
        context_limit_tokens=assembly.context_limit,
        available_tokens=assembly.available,
        prompt_tokens=assembly.token_total,
        reply_reserve_tokens=assembly.max_length,
        example_count=assembly.example_count,
        memory_count=len(assembly.memories),
        summary_count=len(assembly.summary_ids),
        gap_count=len(assembly.gaps),
        history_count=len(assembly.history_seqs),
        reduction_count=len(assembly.reductions),
        truncated_count=int(assembly.current_truncated),
        **sizes,
    )
    if not assembly.fits:
        logger.warning(
            "context_over_budget",
            job_id=job_id,
            conversation_id=conversation_id,
            prompt_tokens=assembly.token_total,
            available_tokens=assembly.available,
        )


class ChatInferenceService:
    """Runs one reply per call, as the JobHandler of `chat_reply` and `chat_regenerate` (lyrveil.jobs.handlers).

    `runtime` must be used only under the run's lease, which the scheduler holds. `events` receives each event
    after the change it announces was committed. `extraction` sets the trigger threshold, and `queue` is where the
    trigger queues extraction jobs. `context` holds the prompt budgets and `retriever` finds the memories (by default
    an FtsMemoryRetriever on `engine`). `monotonic` and `flush_interval_s` time the flushes and `clock` gives the
    aware time of retrieval and use counters; tests pass fakes. The selection of a chat's last reply is kept in memory
    for its next one, so a restart only loses that small bonus. The state a run leaves, besides the job's own:

    - finished: variant and message `complete` with the whole normalized text and the variant's `checks_json`
      (`_checks_record`), or `interrupted` when the runtime reports that someone else aborted the generation; then
      `message.completed`, then the use of its memories, then a
      `memory_extraction` job when the chat is due and a `summary_checkpoint` job when a summary is due;
    - failed with a retry coming (a retryable failure with attempts left, a lost lease, a preemption):
      message back to `queued` with empty text, so the next run starts clean; then `message.updated`;
    - failed for good: variant and message `failed` with the text released so far; then `message.failed`;
    - blocked by V4: `message.restarted`, then variant and message `failed` with BLOCKED_NOTICE and their
      `checks_json`, then `message.updated` and `message.failed` with CONTENT_POLICY_BLOCKED; empty twice (V1): the
      same with empty text, no `message.restarted` and REPLY_EMPTY;
    - cancelled by the shutdown (`close`): the released text flushed, the reply left `streaming` for the next start.

    One run makes at most three generations: the first, one corrective generation after a V6, V10 or V11 hit, and one
    more after an empty reply. Each discarded generation that had shown text is announced with `message.restarted` and
    its text is cleared from the database before the next one starts. Text the normalizer still held back when a run
    failed or was cancelled is dropped with it.
    """

    def __init__(
        self,
        engine: Engine,
        runtime: TextRuntime,
        events: EventPublisher,
        settings: ReplySettings | None = None,
        *,
        extraction: ExtractionSettings | None = None,
        summaries: SummarySettings | None = None,
        context: ContextSettings | None = None,
        retriever: FtsMemoryRetriever | None = None,
        queue: JobQueue | None = None,
        flush_interval_s: float = FLUSH_INTERVAL_S,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._events = events
        self._settings = settings or ReplySettings()
        self._extraction = extraction or ExtractionSettings()
        self._summaries = summaries or SummarySettings()
        self._retriever = retriever or FtsMemoryRetriever(engine)
        self._builder = ContextBuilder(runtime, self._retriever, context)
        self._queue = queue or JobQueue()
        self._flush_interval_s = flush_interval_s
        self._monotonic = monotonic
        self._clock = clock
        self._closing = False
        # The memory selection of each chat's last finished reply, the `previous_ids` of its next one.
        self._previous_memories: dict[str, frozenset[str]] = {}

    def close(self) -> None:
        """Marks the backend as shutting down. A run cancelled from now on keeps its reply `streaming` with the text
        written so far, instead of treating the cancellation as a lost lease. Call it before cancelling the worker."""
        self._closing = True

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        """Generates the reply of `job` and returns JobDone with its message and variant ids.

        A job whose variant is no longer waiting (finished by a run whose job state a restart lost, replaced,
        or deleted) is done at once with `skipped`, without touching the runtime; one whose chat was deleted
        is marked failed first. Raises what the scheduler retries or records, a TextRuntimeError, or
        JobFailedError when no user message is left to answer, after storing the state described on the class.
        """
        reply = await to_thread.run_sync(self._claim, job)
        if reply is None:
            return JobDone({"skipped": True})
        self._events.publish(TYPING_STARTED, _presence(reply))
        streamed: list[str] = []
        try:
            generated = await self._generate(job, reply, context, streamed)
        except anyio.get_cancelled_exc_class():
            with anyio.CancelScope(shield=True):
                await self._cancelled(job, reply, "".join(streamed))
            raise
        except Exception as error:
            with anyio.CancelScope(shield=True):
                await self._failed(job, reply, "".join(streamed), error)
            raise
        message = await to_thread.run_sync(self._finish, reply, generated)
        self._events.publish(TYPING_STOPPED, _presence(reply))
        self._events.publish(MESSAGE_COMPLETED, {"message": message_document(message)})
        logger.info(
            "reply_finished",
            job_id=job.id,
            conversation_id=reply.conversation_id,
            state=generated.status.value,
            reply_chars=len(generated.text),
            prompt_tokens=generated.prompt_tokens,
        )
        self._previous_memories[reply.conversation_id] = frozenset(generated.memory_ids)
        if generated.memory_ids:
            try:
                await to_thread.run_sync(self._retriever.record_use, generated.memory_ids, self._clock())
            except Exception:
                # Use counters only tune recency of use; the stored reply must not become a failed job over them.
                logger.error("memory_use_failed", job_id=job.id, exc_info=True)
        try:
            await to_thread.run_sync(self._queue_extraction, reply.conversation_id, reply.seq)
        except Exception:
            # The reply is already stored and announced: a failing trigger must not turn it into a failed job. The next
            # reply checks the trigger again.
            logger.error("memory_extraction_trigger_failed", job_id=job.id, exc_info=True)
        return JobDone({"message_id": reply.message_id, "variant_id": reply.variant_id})

    def _queue_extraction(self, conversation_id: str, seq: int) -> None:
        """The extraction trigger of docs/backend.md (Job handlers), in its own transaction after the reply's commit:
        queues a `memory_extraction` job when the chat is due for one, a `summary_checkpoint` job when a summary is
        due, and a `memory_reevaluation` job when extraction had already passed the reply's `seq` (a regeneration).
        Blocks: run it in a thread."""

        def work(connection: Connection) -> None:
            queue_extraction_if_due(connection, self._queue, conversation_id, self._extraction)
            queue_summary_if_due(connection, self._queue, conversation_id, self._summaries)
            queue_reevaluation(connection, self._queue, conversation_id, seq)

        write(self._engine, work)

    def _claim(self, job: Job) -> _Reply | None:
        """Marks the job's reply `streaming` with empty text and returns it, or None when it is not waiting."""
        named = job.payload.get("variant_id")
        if not isinstance(named, str) or job.message_id is None:
            return None
        variant_id: str = named
        message_id: str = job.message_id

        def work(connection: Connection) -> _Reply | None:
            message = repository.get_message(connection, message_id)
            variant = repository.get_variant(connection, variant_id)
            if (
                message is None
                or variant is None
                or message.deleted_at is not None
                or message.active_variant_id != variant_id
                or message.status not in PENDING
                or variant.status is not VariantStatus.STREAMING
            ):
                return None
            conversation = conversation_repository.get(connection, message.conversation_id)
            assert conversation is not None
            reply = _Reply(
                message_id=message.id,
                variant_id=variant_id,
                seq=message.seq,
                conversation_id=conversation.id,
                character_id=conversation.character_id,
            )
            if conversation.status is ConversationStatus.DELETED:
                # A deleted chat is hidden everywhere: its reply ends failed instead of taking the GPU.
                _store(connection, reply, "", MessageStatus.FAILED, VariantStatus.FAILED)
                return None
            _store(connection, reply, "", MessageStatus.STREAMING, VariantStatus.STREAMING)
            return reply

        return write(self._engine, work)

    async def _generate(self, job: Job, reply: _Reply, context: RunContext, streamed: list[str]) -> _Generated:
        """Builds the prompt and streams generations (`_stream`) until one is delivered, under the reply checks.

        A V6, V10 or V11 hit in the first generation discards it (`_restart`), and the prompt is built again with the
        check's note first in the anchor, then the adaptive notes, MAX_NOTES at most. Only V4 stops that corrective
        generation, so a second hit is delivered and recorded. An empty reply (V1) is generated once more from the same
        prompt. Raises _CheckFailedError REPLY_EMPTY for a second empty reply and CONTENT_POLICY_BLOCKED for a V4 hit.
        `streamed` collects the text the current generation released, so a failure handler sees what is shown.
        """
        wanted = await to_thread.run_sync(self._load_context, reply)
        if wanted is None:
            raise JobFailedError(INTERNAL, _NOTHING_TO_ANSWER, retryable=False)
        formatter = formatter_for(self._settings.template)
        rules = CheckRules.for_chat(wanted.card, wanted.user, wanted.language)
        previous = [message.content for message in wanted.history if message.author is Author.CHARACTER]
        restarts: list[str] = []
        corrected = False
        assembly = await self._assemble(job, reply, wanted, formatter)
        request = self._request(assembly, formatter, context.genkey)
        while True:
            pipeline = _Pipeline(
                TurnCutoff(formatter.control_texts(), wanted.user.display_name),
                ReplyNormalizer(NormalizerRules.for_card(wanted.card)),
                SentenceSplitter(),
            )
            done = await self._stream(reply, request, pipeline, rules, BLOCKING if corrected else STOPPING, streamed)
            stopped = done.stopped
            if stopped is not None and stopped in BLOCKING:
                record = _checks_record({}, {stopped: 1}, restarts)
                raise _CheckFailedError(CONTENT_POLICY_BLOCKED, stopped, BLOCKED_NOTICE, record)
            if stopped is not None:
                restarts.append(stopped)
                corrected = True
                await self._restart(job, reply, stopped, streamed)
                notes = (note_text(stopped, wanted.card, wanted.user, wanted.language), *wanted.notes)
                assembly = await self._assemble(job, reply, replace(wanted, notes=notes[:MAX_NOTES]), formatter)
                request = self._request(assembly, formatter, context.genkey)
                continue
            normalized = done.normalized
            assert normalized is not None
            cut_check = pipeline.cutoff.cut
            cut: dict[str, int] = {} if cut_check is None else {cut_check: 1}
            if not normalized.text.strip():
                if "V1" in restarts:
                    raise _CheckFailedError(REPLY_EMPTY, "V1", "", _checks_record(normalized.fired, cut, restarts))
                restarts.append("V1")
                await self._restart(job, reply, "V1", streamed)
                continue
            found = check_reply(
                normalized.text, rules, previous=previous[-PREVIOUS_REPLIES:], user_message=wanted.current.content
            )
            checks = dict(sorted({**found, **cut}.items(), key=lambda item: int(item[0][1:])))
            status = MessageStatus.INTERRUPTED if done.finish == "aborted" else MessageStatus.COMPLETE
            memory_ids = tuple(item.id for item in assembly.memories)
            record = _checks_record(normalized.fired, checks, restarts)
            return _Generated(normalized.text, status, assembly.token_total, memory_ids, record)

    async def _assemble(self, job: Job, reply: _Reply, wanted: ContextRequest, formatter: PromptFormatter) -> Assembly:
        """Builds the prompt of `wanted` and logs its counts."""
        assembly = await self._builder.build(wanted, formatter, self._settings.max_length)
        _log_assembly(job.id, reply.conversation_id, assembly)
        return assembly

    def _request(self, assembly: Assembly, formatter: PromptFormatter, genkey: str) -> TextRequest:
        """Returns the runtime request of a built prompt, with the reply settings' samplers and the family's stop
        sequences."""
        settings = self._settings
        return TextRequest(
            prompt=assembly.prompt.text,
            max_context_length=assembly.context_limit,
            max_length=assembly.max_length,
            genkey=genkey,
            temperature=settings.temperature,
            top_p=settings.top_p,
            top_k=settings.top_k,
            min_p=settings.min_p,
            rep_pen=settings.rep_pen,
            dry_multiplier=settings.dry_multiplier,
            dry_base=settings.dry_base,
            dry_allowed_length=settings.dry_allowed_length,
            dry_penalty_last_n=settings.dry_penalty_last_n,
            dry_sequence_breakers=settings.dry_sequence_breakers,
            stop_sequence=formatter.stop_sequences(settings.user.display_name),
        )

    async def _stream(
        self,
        reply: _Reply,
        request: TextRequest,
        pipeline: _Pipeline,
        rules: CheckRules,
        stop_on: frozenset[str],
        streamed: list[str],
    ) -> _Pass:
        """Streams one generation of `request` into the reply: the path every generated reply text takes.

        Each runtime token goes through the pipeline's cut (V2, V3) and normalizer. The sentences the released text
        completes are checked first; then the text is appended to `streamed`, published as a `message.delta` and
        flushed to the database every `flush_interval_s` seconds, so nothing the normalizer holds back or removes is
        ever shown or stored. A sentence that breaks a check of `stop_on` stops the generation: the text that
        completed it is not published, the runtime is asked to abort, the rest of the stream is read and dropped, and
        the pass names the check. A cut aborts the runtime too, and the reply ends at the cut with the finish reason
        `stop`. At the end the held text is checked and published as well, and the pass holds the delivered text and
        the finish reason. When N4 cuts a reply that the length limit stopped, no delta can take back text already
        shown: `message.completed` carries the delivered text, which replaces it. Raises RuntimeStreamDroppedError
        when the stream ends without its final chunk, or what the runtime raises.
        """
        offset = 0
        finish: FinishReason | None = None
        stopped: str | None = None
        ended = False  # This run asked the runtime to abort: the rest of the stream is dropped.
        last_flush = self._monotonic()
        async for chunk in self._runtime.stream(request):
            if chunk.final:
                finish = chunk.finish_reason
                continue
            if ended:
                continue
            released = pipeline.normalizer.feed(pipeline.cutoff.feed(chunk.token))
            stopped = first_stop(pipeline.sentences.feed(released), rules, stop_on)
            if stopped is None:
                offset = self._publish(reply, released, offset, streamed)
            if stopped is not None or pipeline.cutoff.cut is not None:
                ended = True
                await self._runtime.abort(request.genkey)
            elif self._monotonic() - last_flush >= self._flush_interval_s:
                await to_thread.run_sync(self._flush, reply, "".join(streamed))
                last_flush = self._monotonic()
        if finish is None:
            raise RuntimeStreamDroppedError("the stream ended without its final chunk")
        if stopped is not None:
            return _Pass(None, finish, stopped)
        if pipeline.cutoff.cut is not None:
            finish = "stop"
        held = pipeline.normalizer.feed(pipeline.cutoff.finish())
        normalized = pipeline.normalizer.finish(finish)
        last = held + normalized.tail
        stopped = first_stop([*pipeline.sentences.feed(last), *pipeline.sentences.finish()], rules, stop_on)
        if stopped is not None:
            return _Pass(None, finish, stopped)
        self._publish(reply, last, offset, streamed)
        return _Pass(normalized, finish)

    async def _restart(self, job: Job, reply: _Reply, check: str, streamed: list[str]) -> None:
        """Discards the generation that `check` stopped before the next one starts: the stored text is cleared and
        `message.restarted` is published, so the client drops the bubble's text and the next deltas start again at
        offset 0. An empty reply (V1) shows no text, so its retry publishes nothing."""
        logger.info("reply_restarted", job_id=job.id, conversation_id=reply.conversation_id, check=check)
        if check == "V1" and not streamed:
            return
        streamed.clear()
        await to_thread.run_sync(self._flush, reply, "")
        restarted = {"message_id": reply.message_id, "variant_id": reply.variant_id, "check": check}
        self._events.publish(MESSAGE_RESTARTED, restarted)

    def _publish(self, reply: _Reply, text: str, offset: int, streamed: list[str]) -> int:
        """Publishes `text` as the reply's next `message.delta` at `offset` and appends it to `streamed`, unless it
        is empty. Returns the offset after it, in UTF-16 code units."""
        if not text:
            return offset
        streamed.append(text)
        delta = {"message_id": reply.message_id, "variant_id": reply.variant_id, "offset": offset}
        self._events.publish(MESSAGE_DELTA, {**delta, "text": text})
        return offset + _utf16_length(text)

    def _load_context(self, reply: _Reply) -> ContextRequest | None:
        """Returns what the reply's prompt is built from (load_context_request), in one read transaction. Blocks: run
        it in a thread."""
        previous = self._previous_memories.get(reply.conversation_id, frozenset())
        return read(
            self._engine,
            lambda connection: load_context_request(
                connection,
                reply.conversation_id,
                reply.seq,
                user=self._settings.user,
                now=self._clock(),
                previous_memory_ids=previous,
            ),
        )

    def _write(
        self,
        reply: _Reply,
        text: str,
        status: MessageStatus,
        variant_status: VariantStatus,
        checks: Mapping[str, object] | None = None,
    ) -> Message:
        """Stores `text`, the statuses and, when given, `checks` in one transaction and returns the message. Blocks:
        run it in a thread."""
        return write(self._engine, lambda connection: _store(connection, reply, text, status, variant_status, checks))

    def _flush(self, reply: _Reply, text: str) -> None:
        self._write(reply, text, MessageStatus.STREAMING, VariantStatus.STREAMING)

    def _finish(self, reply: _Reply, generated: _Generated) -> Message:
        status = generated.status
        return self._write(reply, generated.text, status, VariantStatus(status.value), generated.checks)

    async def _cancelled(self, job: Job, reply: _Reply, text: str) -> None:
        if self._closing:
            # Shutdown: keep what was streamed and leave the reply `streaming`; the next start marks it interrupted
            # and ends its job, as after a crash.
            await to_thread.run_sync(self._flush, reply, text)
            return
        # The scheduler's only other cancellation is a lost lease, which it retries with LEASE_LOST.
        await self._settle(job, reply, text, LEASE_LOST, retry=job.attempt < job.max_attempts)

    async def _failed(self, job: Job, reply: _Reply, text: str, error: Exception) -> None:
        if isinstance(error, _CheckFailedError):
            await self._check_failed(job, reply, error)
            return
        if isinstance(error, JobPreemptedError):
            # Preemption costs no attempt: the job always runs again.
            await self._settle(job, reply, text, LEASE_LOST, retry=True)
            return
        code, retryable = _failure(error)
        await self._settle(job, reply, text, code, retry=retryable and job.attempt < job.max_attempts)

    async def _check_failed(self, job: Job, reply: _Reply, error: _CheckFailedError) -> None:
        """Stores the end of a run that a reply check failed: variant and message `failed` with the error's text and
        `checks_json`. Then publishes `message.restarted` for V4, so the client drops the words it streamed,
        `typing.stopped`, `message.updated` with the stored message, so the client shows the notice, and
        `message.failed`."""
        message = await to_thread.run_sync(
            self._write, reply, error.text, MessageStatus.FAILED, VariantStatus.FAILED, error.checks
        )
        if error.check in BLOCKING:
            restarted = {"message_id": reply.message_id, "variant_id": reply.variant_id, "check": error.check}
            self._events.publish(MESSAGE_RESTARTED, restarted)
        self._events.publish(TYPING_STOPPED, _presence(reply))
        self._events.publish(MESSAGE_UPDATED, {"message": message_document(message)})
        self._events.publish(MESSAGE_FAILED, {"message_id": reply.message_id, "error_code": error.code})
        logger.info(
            "reply_failed",
            job_id=job.id,
            conversation_id=reply.conversation_id,
            error_code=error.code,
            check=error.check,
        )

    async def _settle(self, job: Job, reply: _Reply, text: str, code: str, *, retry: bool) -> None:
        """Stores the end of a failed run: back to `queued` with empty text when the scheduler will run it
        again, the same rule as JobQueue.fail, or `failed` with the streamed text otherwise. Then publishes
        the change."""
        stored, status, variant_status = (
            ("", MessageStatus.QUEUED, VariantStatus.STREAMING)
            if retry
            else (text, MessageStatus.FAILED, VariantStatus.FAILED)
        )
        message = await to_thread.run_sync(self._write, reply, stored, status, variant_status)
        self._events.publish(TYPING_STOPPED, _presence(reply))
        if retry:
            self._events.publish(MESSAGE_UPDATED, {"message": message_document(message)})
        else:
            self._events.publish(MESSAGE_FAILED, {"message_id": reply.message_id, "error_code": code})
        logger.info("reply_failed", job_id=job.id, conversation_id=reply.conversation_id, error_code=code)


def recover_interrupted_replies(engine: Engine, queue: JobQueue) -> list[str]:
    """Startup step 5 of docs/backend.md, the variant half, and returns the ids of the replies it marked.

    Every message still `streaming` was cut by a crash or a shutdown: it and its active variant become
    `interrupted` and keep the text written so far, and its running reply job ends `failed` with
    JOB_INTERRUPTED instead of being queued again, so the reply is not silently replaced; the interface
    offers regeneration. A reply still `queued` is left alone, and `recover_interrupted_jobs`, which must run
    after this function, queues its job again. Requires that no worker runs yet. Runs in one transaction.
    """

    def work(connection: Connection) -> list[str]:
        marked: list[str] = []
        for message in repository.streaming_messages(connection):
            repository.update_message(connection, message.id, status=MessageStatus.INTERRUPTED.value)
            if message.active_variant_id is not None:
                repository.update_variant(connection, message.active_variant_id, status=VariantStatus.INTERRUPTED.value)
            for job_id in repository.running_reply_jobs(connection, message.id):
                queue.fail(connection, job_id, JOB_INTERRUPTED, None, retryable=False)
            marked.append(message.id)
        return marked

    marked = write(engine, work)
    logger.info("replies_recovered", message_count=len(marked))
    return marked
