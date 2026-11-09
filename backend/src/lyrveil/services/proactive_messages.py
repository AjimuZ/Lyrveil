"""ProactiveMessageService: the handler of `proactive_message`, which decides whether a character writes first and, if
so, writes the message (docs/scheduler.md, Generation flow; ADR 0020, ADR 0031).

A run has two stages. `decide` asks the model for a ProactiveDecision under its grammar (one repair, like every
structured task); a `scheduled_message` skips it, because the user asked for it. When `send` is false the event ends
`skipped` with the reason and no message exists. `generate` builds the reply prompt with no current message: the same
stable text and history as a reply, a dynamic context frame closed by the instruction of lyrveil.prompts.proactive, the
`brevi` length band or the character's when it is shorter. The text passes the V2 and V3 cut, the card's normalizer
and the reply checks exactly as a reply does, with the same corrective generation after a V6, V10 or V11 hit and one
more after an empty text. A text with a similarity of 0.8 or more to one of the last 20 proactive messages of the chat
(lyrveil.scheduling.repetition) is discarded and generated once more; a second hit, a V4 hit and a second empty text
end the event `skipped`.

A delivered message is written whole in one transaction with the event's end: `created_at` is the real generation time,
`scheduled_event_id` links it to its event and the event becomes `delivered`. Nothing streams, so the client gets one
`message.completed`. The unread count follows from the new message, and a `proactive_message` notification is written
in the same transaction and announced with `notification.created` right after (P07.5). A failure that will not be
retried ends the event `failed`, a cancellation `cancelled`; a run the scheduler retries or resumes after a restart
finds its event still `running`. No message, memory, reason or prompt is ever logged.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from typing import Any

import anyio
from anyio import to_thread
from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text

from lyrveil.consistency.checks import (
    BLOCKING,
    PREVIOUS_REPLIES,
    STOPPING,
    CheckRules,
    SentenceSplitter,
    TurnCutoff,
    check_reply,
    first_stop,
    note_text,
)
from lyrveil.consistency.normalizer import NormalizedReply, NormalizerRules, ReplyNormalizer
from lyrveil.context.blocks import MAX_NOTES, proactive_band
from lyrveil.context.builder import ContextBuilder, ContextRequest, ContextSettings
from lyrveil.domain.conversations import ConversationStatus
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import (
    Author,
    Message,
    MessageStatus,
    VariantSource,
    VariantStatus,
    content_hash,
    message_document,
)
from lyrveil.domain.notifications import Notification
from lyrveil.domain.proactive import ProactiveDecision
from lyrveil.jobs.handlers import (
    INTERNAL,
    JobDone,
    JobFailedError,
    JobOutcome,
    JobPreemptedError,
    RunContext,
)
from lyrveil.jobs.queue import Job
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import messages as repository
from lyrveil.persistence import notifications as notification_repository
from lyrveil.persistence import settings as settings_repository
from lyrveil.ports.events import MESSAGE_COMPLETED, EventPublisher
from lyrveil.ports.memory_retriever import RetrievalQuery
from lyrveil.ports.text_runtime import (
    FinishReason,
    RuntimeStreamDroppedError,
    TextRequest,
    TextRuntime,
    TextRuntimeError,
)
from lyrveil.prompts import proactive as prompts
from lyrveil.prompts.extraction import PromptMessage
from lyrveil.prompts.formatter import PromptFormatter, formatter_for
from lyrveil.prompts.gbnf import schema_to_gbnf
from lyrveil.prompts.structured import (
    STRUCTURED_OUTPUT_INVALID,
    OutputRejectedError,
    StructuredOutputInvalidError,
    generate_structured,
)
from lyrveil.scheduling import store
from lyrveil.scheduling.repetition import RECENT_PROACTIVE, is_repetition
from lyrveil.scheduling.rules import EventKind, parse_settings
from lyrveil.services.chat_inference import ReplySettings, load_context_request, reply_request
from lyrveil.services.memory_retrieval import FtsMemoryRetriever
from lyrveil.services.notifications import announce

# Generated once from the schema, so the grammar always follows the model.
DECISION_GRAMMAR = schema_to_gbnf(ProactiveDecision)
# Every readable message of the chat is history for a proactive message: no message is "current".
_BEFORE_ALL = 2**62
# The decision shows this many newest messages and memories.
DECISION_MESSAGES = 6
DECISION_MEMORIES = 6
DECISION_MEMORY_TOKENS = 400
# The reason given to the generation when the user scheduled the message without a note, or the model gave no reason.
SCHEDULED_REASON = "Hai deciso tu di scrivere a quest'ora."
DEFAULT_REASON = "Hai voglia di scrivere."
# The notification quotes this many characters of the message.
PREVIEW_CHARS = 140

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Ready:
    """A run that has an event to serve: the event, and what its prompts are built from."""

    event: store.RunningEvent
    wanted: ContextRequest
    rules: CheckRules


@dataclass(frozen=True)
class _Ended:
    """A run that needs no generation, or that ended its event without a message. `status` is the job result's."""

    status: str
    reason: str | None = None


@dataclass(frozen=True)
class _Written:
    """A generated message that passed every check: its text and the `checks_json` of its variant."""

    text: str
    checks: Mapping[str, object]
    prompt_tokens: int
    attempts: int


@dataclass(frozen=True)
class _Pass:
    """One generation: `stopped` names the check that discarded it, otherwise `normalized` is the delivered text."""

    normalized: NormalizedReply | None
    cut: str | None = None
    stopped: str | None = None


@dataclass(frozen=True)
class _Delivery:
    """A delivered message and the notification written with it."""

    message: Message
    notification: Notification


def _preview(text_: str) -> str:
    """Returns the start of the message for the notice: whitespace folded, at most PREVIEW_CHARS characters, with an
    ellipsis when it was cut."""
    flat = " ".join(text_.split())
    if len(flat) <= PREVIEW_CHARS:
        return flat
    return flat[: PREVIEW_CHARS - 1].rstrip() + "…"


class _EventEndedError(Exception):
    """Raised inside the delivery transaction to roll it back; its text says why: `not_running` (the event ended while
    the model ran) or `conversation_unavailable` (the chat was deleted or archived)."""


def _failure(error: Exception) -> tuple[str, bool]:
    """Returns the job error code of a failed run and whether the scheduler retries that kind of failure."""
    if isinstance(error, TextRuntimeError):
        return error.code, True
    if isinstance(error, JobFailedError):
        return error.code, error.retryable
    return INTERNAL, False


def validate_decision(raw: dict[str, Any]) -> ProactiveDecision:
    """Returns the parsed output as a ProactiveDecision. Raises OutputRejectedError with an Italian detail that names
    the field and never the value when it does not match the schema."""
    try:
        return ProactiveDecision.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        path = ".".join(str(part) for part in first["loc"])
        if not path:
            raise OutputRejectedError("La risposta non è una decisione valida.") from None
        if first["type"] == "missing":
            raise OutputRejectedError(f"Manca il campo «{path}».") from None
        raise OutputRejectedError(f"Il campo «{path}» non è valido.") from None


def message_card(wanted: ContextRequest) -> ContextRequest:
    """Returns `wanted` with the card's length band replaced by the proactive one (`brevi`, or the card's band when it
    is shorter), so the reply reserve and the anchor's length text agree with the instruction."""
    communication = wanted.card.communication
    band = proactive_band(communication.message_length)
    if band == communication.message_length:
        return wanted
    card = wanted.card.model_copy(update={"communication": communication.model_copy(update={"message_length": band})})
    return replace(wanted, card=card)


class ProactiveMessageService:
    """Runs one proactive message per call, as the JobHandler of `proactive_message` (lyrveil.jobs.handlers).

    `runtime` must be used only under the run's lease, which the scheduler holds. `events` receives each event after
    the change it announces was committed. `settings` give the template family, the user and the samplers of a reply,
    `context` the prompt budgets and `retriever` finds the memories (by default an FtsMemoryRetriever on `engine`).
    `clock` returns the aware current time; tests pass a fake one. `zone` is `LYRVEIL_TZ`, for the time context of
    block 4. The handler also ends the event of a job that failed
    for good (`abandon`) or was cancelled (`cancelled`).
    """

    def __init__(
        self,
        engine: Engine,
        runtime: TextRuntime,
        events: EventPublisher,
        settings: ReplySettings | None = None,
        *,
        context: ContextSettings | None = None,
        retriever: FtsMemoryRetriever | None = None,
        clock: Callable[[], datetime] = _utc_now,
        zone: tzinfo = UTC,
    ) -> None:
        self._zone = zone
        self._engine = engine
        self._runtime = runtime
        self._events = events
        self._settings = settings or ReplySettings()
        self._retriever = retriever or FtsMemoryRetriever(engine)
        self._builder = ContextBuilder(runtime, self._retriever, context)
        self._clock = clock

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        """Serves the event the job's payload names and returns JobDone with a `status`.

        `status` is `delivered` (with `message_id`), `skipped` (with `reason`: the model's own, or `repetition`,
        `empty`, `blocked`, `switched_off`), `cancelled` (the chat is gone or archived) or `not_running` (the event
        already ended: a restart ran the stage twice). Raises JobFailedError STRUCTURED_OUTPUT_INVALID, retryable,
        when the decision and its repair are both refused, INTERNAL for a payload without an event, and lets a
        TextRuntimeError through for the scheduler to retry. A failure the scheduler will not retry ends the event
        `failed` first.
        """
        event_id = job.payload.get("event_id")
        if not isinstance(event_id, str):
            raise JobFailedError(INTERNAL, None, retryable=False)
        try:
            return await self._serve(job, context, event_id)
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as error:
            code, retryable = _failure(error)
            if isinstance(error, JobPreemptedError) or (retryable and job.attempt < job.max_attempts):
                raise
            with anyio.CancelScope(shield=True):
                await to_thread.run_sync(self._end, event_id, "failed", code)
            raise

    async def abandon(self, job: Job, error: JobFailedError) -> None:
        """Ends the event of a job that never got the GPU (AbandoningHandler): `failed` with the error's code."""
        event_id = job.payload.get("event_id")
        if isinstance(event_id, str):
            await to_thread.run_sync(self._end, event_id, "failed", error.code)

    async def cancelled(self, job: Job) -> None:
        """Ends the event of a cancelled job (CancellingHandler): `cancelled`, so it no longer holds the chat."""
        event_id = job.payload.get("event_id")
        if isinstance(event_id, str):
            await to_thread.run_sync(self._end, event_id, "cancelled", "job_cancelled")

    async def _serve(self, job: Job, context: RunContext, event_id: str) -> JobOutcome:
        prepared = await to_thread.run_sync(self._prepare, event_id)
        if isinstance(prepared, _Ended):
            return JobDone({"status": prepared.status, "reason": prepared.reason})
        event, wanted = prepared.event, prepared.wanted
        user = wanted.user.display_name
        if event.kind is EventKind.SCHEDULED_MESSAGE:
            reason = event.note or SCHEDULED_REASON
        else:
            decision = await self._decide(prepared, context.genkey)
            if not decision.send:
                await to_thread.run_sync(self._end, event.id, "skipped", decision.reason)
                logger.info("proactive_skipped", job_id=job.id, conversation_id=event.conversation_id, state="decided")
                return JobDone({"status": "skipped", "reason": decision.reason})
            reason = decision.reason.strip() or DEFAULT_REASON
        wanted = message_card(replace(wanted, instruction=prompts.instruction(user, reason)))
        outcome = await self._compose(job, replace(prepared, wanted=wanted), context.genkey)
        if isinstance(outcome, _Ended):
            await to_thread.run_sync(self._end, event.id, "skipped", outcome.reason)
            logger.info("proactive_skipped", job_id=job.id, conversation_id=event.conversation_id, state=outcome.reason)
            return JobDone({"status": "skipped", "reason": outcome.reason})
        result = await to_thread.run_sync(self._deliver, event, outcome, wanted.card.identity.first_name)
        if result is None:
            return JobDone({"status": "not_running"})
        delivered = result.message
        self._events.publish(MESSAGE_COMPLETED, {"message": message_document(delivered)})
        announce(self._events, result.notification)
        logger.info(
            "proactive_delivered",
            job_id=job.id,
            conversation_id=event.conversation_id,
            event_kind=event.kind.value,
            reply_chars=len(outcome.text),
            prompt_tokens=outcome.prompt_tokens,
            attempt_count=outcome.attempts,
        )
        return JobDone({"status": "delivered", "message_id": delivered.id})

    # Preparing

    def _prepare(self, event_id: str) -> _Ready | _Ended:
        """Reads the event and what the prompts are built from. Ends the event `cancelled` when its chat is deleted or
        archived, and `skipped` when the user switched the feature off since the job was queued. Blocks."""

        def work(connection: Connection) -> _Ready | _Ended:
            event = store.get_event(connection, event_id)
            if event is None or event.status != "running":
                return _Ended("not_running")
            now = to_utc_text(self._clock())
            conversation = conversation_repository.get(connection, event.conversation_id)
            if conversation is None or conversation.status is not ConversationStatus.ACTIVE:
                store.finish(connection, event_id, "cancelled", now, reason="conversation_unavailable")
                return _Ended("cancelled", "conversation_unavailable")
            if not self._allowed(connection, event.conversation_id):
                store.finish(connection, event_id, "skipped", now, reason="switched_off")
                return _Ended("skipped", "switched_off")
            wanted = load_context_request(
                connection,
                event.conversation_id,
                _BEFORE_ALL,
                user=self._settings.user,
                now=self._clock(),
                instruction="",
                hint=event.note or "",
                zone=self._zone,
            )
            assert wanted is not None, "a proactive request always loads"
            return _Ready(event, wanted, CheckRules.for_chat(wanted.card, wanted.user, wanted.language))

        return write(self._engine, work)

    @staticmethod
    def _allowed(connection: Connection, conversation_id: str) -> bool:
        """Tells whether the global switch and the conversation's own setting are both still on."""
        if not settings_repository.get_flag(connection, settings_repository.PROACTIVE_ENABLED, False):
            return False
        stored = connection.execute(
            text("SELECT proactive_json FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).scalar()
        return parse_settings(stored).enabled

    # Stage decide

    async def _decide(self, prepared: _Ready, genkey: str) -> ProactiveDecision:
        """Asks the model whether the character writes, with the state, memories, open threads and newest messages.
        Raises JobFailedError STRUCTURED_OUTPUT_INVALID, retryable, when the decision and its one repair are refused."""
        event, wanted = prepared.event, prepared.wanted
        memories = await to_thread.run_sync(self._memories, wanted)
        names = {Author.USER: wanted.user.display_name, Author.CHARACTER: wanted.card.identity.first_name}
        data = prompts.DecisionPrompt(
            user_name=wanted.user.display_name,
            character_name=wanted.card.identity.first_name,
            language=wanted.language,
            kind=event.kind.value,
            note=event.note,
            state=wanted.state.lines,
            memories=memories,
            threads=wanted.state.details,
            recent=[
                PromptMessage(m.seq, names.get(m.author, m.author.value), m.content)
                for m in wanted.history[-DECISION_MESSAGES:]
            ],
        )
        template = self._settings.template
        try:
            result = await generate_structured(
                self._runtime,
                prompt=lambda repair: prompts.render_decision(template, data, repair),
                grammar=DECISION_GRAMMAR,
                validate=validate_decision,
                genkey=genkey,
                max_length=prompts.MAX_LENGTH,
                temperature=prompts.TEMPERATURE,
            )
        except StructuredOutputInvalidError as invalid:
            logger.info("proactive_decision_failed", conversation_id=event.conversation_id)
            raise JobFailedError(STRUCTURED_OUTPUT_INVALID, invalid.detail, retryable=True) from None
        return result.value

    def _memories(self, wanted: ContextRequest) -> list[str]:
        """Returns the rendered lines of the memories that best fit the event's note and the newest messages. Blocks."""
        recent = [message.content for message in wanted.history[-DECISION_MESSAGES:]]
        query = RetrievalQuery(
            character_id=wanted.character_id,
            conversation_id=wanted.conversation_id,
            text="\n".join([*recent, wanted.hint]),
            topic=None,
            language=wanted.language,
            token_budget=DECISION_MEMORY_TOKENS,
            now=wanted.now,
            include_shared=wanted.shares_memory,
            past_intent=False,
            previous_ids=frozenset(),
            max_items=DECISION_MEMORIES,
        )
        return [item.line for item in self._retriever.retrieve_relevant_memories(query).items]

    # Stage generate

    async def _compose(self, job: Job, prepared: _Ready, genkey: str) -> _Written | _Ended:
        """Generates the message under the reply checks and the repetition check, and returns it, or why the event ends
        `skipped` (`blocked` for a V4 hit, `empty` for a second empty text, `repetition` for a second similar text).

        A V6, V10 or V11 hit in a first generation discards it and generates once more with that check's note first in
        the anchor; V4 stops every generation. A discarded generation costs nothing visible: nothing was published.
        At most four generations run: one per kind of discard, and the first.
        """
        wanted, rules = prepared.wanted, prepared.rules
        formatter = formatter_for(self._settings.template)
        recent = await to_thread.run_sync(self._recent_texts, prepared.event.conversation_id)
        previous = [message.content for message in wanted.history if message.author is Author.CHARACTER]
        restarts: list[str] = []
        corrected = emptied = repeated = False
        notes = tuple(wanted.notes)
        attempts = 0
        while True:
            attempts += 1
            assembly = await self._builder.build(replace(wanted, notes=notes), formatter, self._settings.max_length)
            request = reply_request(assembly, formatter, self._settings, genkey)
            done = await self._stream(request, formatter, wanted, rules, BLOCKING if corrected else STOPPING)
            if done.stopped is not None and done.stopped in BLOCKING:
                return _Ended("skipped", "blocked")
            if done.stopped is not None:
                restarts.append(done.stopped)
                corrected = True
                notes = (note_text(done.stopped, wanted.card, wanted.user, wanted.language), *wanted.notes)[:MAX_NOTES]
                continue
            normalized = done.normalized
            assert normalized is not None
            reply = normalized.text.strip()
            if not reply:
                if emptied:
                    return _Ended("skipped", "empty")
                emptied = True
                restarts.append("V1")
                continue
            if is_repetition(reply, recent):
                if repeated:
                    return _Ended("skipped", "repetition")
                repeated = True
                restarts.append("repetition")
                continue
            found = check_reply(reply, rules, previous=previous[-PREVIOUS_REPLIES:])
            cut: dict[str, int] = {} if done.cut is None else {done.cut: 1}
            checks = dict(sorted({**found, **cut}.items(), key=lambda item: int(item[0][1:])))
            record = {
                "normalizer": dict(normalized.fired),
                "checks": checks,
                "restarts": list(restarts),
                "regenerations": len(restarts),
            }
            return _Written(reply, record, assembly.token_total, attempts)

    def _recent_texts(self, conversation_id: str) -> list[str]:
        """Returns the last RECENT_PROACTIVE proactive messages of the chat, newest first. Blocks."""
        return read(
            self._engine, lambda connection: store.recent_proactive_texts(connection, conversation_id, RECENT_PROACTIVE)
        )

    async def _stream(
        self,
        request: TextRequest,
        formatter: PromptFormatter,
        wanted: ContextRequest,
        rules: CheckRules,
        stop_on: frozenset[str],
    ) -> _Pass:
        """Streams one generation through the V2 and V3 cut, the card's normalizer and the sentence checks of `stop_on`,
        and returns the delivered text or the check that discarded it. Nothing is published: the text is delivered whole
        at the end. A stopping check or a cut asks the runtime to abort, and the rest of the stream is read and dropped.
        Raises RuntimeStreamDroppedError when the stream ends without its final chunk."""
        cutoff = TurnCutoff(formatter.control_texts(), wanted.user.display_name)
        normalizer = ReplyNormalizer(NormalizerRules.for_card(wanted.card))
        sentences = SentenceSplitter()
        finish: FinishReason | None = None
        stopped: str | None = None
        ended = False
        async for chunk in self._runtime.stream(request):
            if chunk.final:
                finish = chunk.finish_reason
                continue
            if ended:
                continue
            released = normalizer.feed(cutoff.feed(chunk.token))
            stopped = first_stop(sentences.feed(released), rules, stop_on)
            if stopped is not None or cutoff.cut is not None:
                ended = True
                await self._runtime.abort(request.genkey)
        if finish is None:
            raise RuntimeStreamDroppedError("the stream ended without its final chunk")
        if stopped is not None:
            return _Pass(None, stopped=stopped)
        if cutoff.cut is not None:
            finish = "stop"
        held = normalizer.feed(cutoff.finish())
        normalized = normalizer.finish(finish)
        stopped = first_stop([*sentences.feed(held + normalized.tail), *sentences.finish()], rules, stop_on)
        if stopped is not None:
            return _Pass(None, stopped=stopped)
        return _Pass(normalized, cut=cutoff.cut)

    # Delivering

    def _deliver(self, event: store.RunningEvent, written: _Written, character_name: str) -> _Delivery | None:
        """Writes the message, its variant, the event's end and the `proactive_message` notification in one
        transaction and returns the message with the notification, or None when the event is no longer `running` or its
        chat is gone: then nothing is written. The notice is titled with the character's name and quotes the start of
        the message. Blocks."""
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> _Delivery:
            conversation = conversation_repository.get(connection, event.conversation_id)
            if conversation is None or conversation.status is not ConversationStatus.ACTIVE:
                raise _EventEndedError("conversation_unavailable")
            message_id, variant_id = new_id(), new_id()
            repository.insert_message(
                connection,
                message_id=message_id,
                conversation_id=event.conversation_id,
                seq=repository.take_seq(connection, event.conversation_id, now),
                client_message_id=None,
                author=Author.CHARACTER,
                status=MessageStatus.COMPLETE,
                read_state=None,
                content=written.text,
                now=now,
                scheduled_event_id=event.id,
            )
            repository.insert_variant(
                connection,
                variant_id=variant_id,
                message_id=message_id,
                content=written.text,
                content_hash=content_hash(written.text),
                source=VariantSource.GENERATED,
                status=VariantStatus.COMPLETE,
                now=now,
            )
            repository.update_variant(
                connection, variant_id, checks_json=json.dumps(written.checks, ensure_ascii=False)
            )
            repository.update_message(connection, message_id, active_variant_id=variant_id)
            if not store.finish(connection, event.id, "delivered", now, message_id=message_id):
                # The event ended while the model ran (the job was cancelled): rolling back drops the message.
                raise _EventEndedError("not_running")
            delivered = repository.get_message(connection, message_id)
            assert delivered is not None
            notification_id = notification_repository.insert_notification(
                connection,
                kind=notification_repository.PROACTIVE_MESSAGE,
                title=character_name,
                body=_preview(written.text),
                now=now,
                conversation_id=event.conversation_id,
                message_id=message_id,
            )
            notification = notification_repository.get(connection, notification_id)
            assert notification is not None
            return _Delivery(delivered, notification)

        try:
            return write(self._engine, work)
        except _EventEndedError as ended:
            if str(ended) == "conversation_unavailable":
                self._end(event.id, "cancelled", "conversation_unavailable")
            return None

    def _end(self, event_id: str, status: str, reason: str | None) -> None:
        """Ends the event as `status` with `reason` in its own transaction, unless it already ended. Blocks."""
        now = to_utc_text(self._clock())
        write(self._engine, lambda connection: store.finish(connection, event_id, status, now, reason=reason))
