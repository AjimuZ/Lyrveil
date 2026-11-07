"""ChatInferenceService: the handler of `chat_reply` and `chat_regenerate`, which streams a reply into its variant.

docs/backend.md (Streaming persistence, Job handlers). A run fills the `streaming` variant its job names and
never adds one, so a second run overwrites the same variant. Each token goes to the event hub as
`message.delta` as soon as the runtime yields it. The text is written to the variant and the message every
FLUSH_INTERVAL_S seconds and at the end, each time in its own short transaction, so no transaction stays open
while the reply streams. After a crash the reply keeps what was written: `recover_interrupted_replies` marks it
`interrupted` at the next start.

P02 scope: the prompt is the minimal context of lyrveil.context.minimal, rendered by the formatter of
`ReplySettings.template` and trimmed to the runtime's real context with its own token counts. Tokens are
stored and published as the runtime yields them: ReplyNormalizer, the reply checks and the corrective
generation, and with them `message.restarted`, come in P04.8. No message text, prompt or token is ever logged.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import anyio
from anyio import to_thread
from sqlalchemy import Connection, Engine

from lyrveil.catalog import TextTemplate
from lyrveil.context.minimal import UserProfile, chat_parts, split_current_message
from lyrveil.domain.characters import CharacterProfile
from lyrveil.domain.conversations import ConversationStatus
from lyrveil.domain.messages import PENDING, Message, MessageStatus, VariantStatus, content_hash, message_document
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
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import messages as repository
from lyrveil.ports.events import (
    MESSAGE_COMPLETED,
    MESSAGE_DELTA,
    MESSAGE_FAILED,
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
from lyrveil.prompts.formatter import FormattedPrompt, PromptFormatter, Turn, formatter_for

# docs/backend.md (Streaming persistence): the streamed text reaches the database at least this often.
FLUSH_INTERVAL_S = 2.0
# The newest messages a P02 prompt reads before the token budget trims it; summaries take over in P04.4.
MAX_HISTORY_MESSAGES = 100
_NOTHING_TO_ANSWER = "Non c'è un messaggio a cui rispondere."

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReplySettings:
    """What a reply request takes besides the chat.

    P02 has no settings store yet, so the defaults are the documented ones: the `gemma4` family of the default text
    profiles of both tiers, the `gemma4` chat preset of docs/prompting.md (Sampler presets), `llm.max_length` 400 and
    the default user of docs/configuration.md. A sampler field left at None is not sent.
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
    language: str


@dataclass(frozen=True)
class _Generated:
    text: str
    status: MessageStatus
    prompt_tokens: int


def _presence(reply: _Reply) -> dict[str, str]:
    """Returns the data of `typing.started` and `typing.stopped`."""
    return {"conversation_id": reply.conversation_id, "character_id": reply.character_id}


def _utf16_length(text: str) -> int:
    # Delta offsets count UTF-16 code units, the unit of JavaScript string indices in the browser client.
    return len(text.encode("utf-16-le")) // 2


def _store(
    connection: Connection, reply: _Reply, text: str, message_status: MessageStatus, variant_status: VariantStatus
) -> Message:
    """Writes `text` and the statuses into the reply's variant and message, and returns the message."""
    repository.update_variant(
        connection, reply.variant_id, content=text, content_hash=content_hash(text), status=variant_status.value
    )
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


class ChatInferenceService:
    """Runs one reply per call, as the JobHandler of `chat_reply` and `chat_regenerate` (lyrveil.jobs.handlers).

    `runtime` must be used only under the run's lease, which the scheduler holds. `events` receives each event
    after the change it announces was committed. `monotonic` and `flush_interval_s` time the flushes; tests
    pass fakes. The state a run leaves, besides the job's own:

    - finished: variant and message `complete` with the whole text, or `interrupted` when the runtime
      reports the generation aborted; then `message.completed`;
    - failed with a retry coming (a retryable failure with attempts left, a lost lease, a preemption):
      message back to `queued` with empty text, so the next run starts clean; then `message.updated`;
    - failed for good: variant and message `failed` with the text streamed so far; then `message.failed`;
    - cancelled by the shutdown (`close`): the text flushed, the reply left `streaming` for the next start.
    """

    def __init__(
        self,
        engine: Engine,
        runtime: TextRuntime,
        events: EventPublisher,
        settings: ReplySettings | None = None,
        *,
        flush_interval_s: float = FLUSH_INTERVAL_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._events = events
        self._settings = settings or ReplySettings()
        self._flush_interval_s = flush_interval_s
        self._monotonic = monotonic
        self._closing = False

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
            generated = await self._generate(reply, context, streamed)
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
        return JobDone({"message_id": reply.message_id, "variant_id": reply.variant_id})

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
                language=conversation.effective_language,
            )
            if conversation.status is ConversationStatus.DELETED:
                # A deleted chat is hidden everywhere: its reply ends failed instead of taking the GPU.
                _store(connection, reply, "", MessageStatus.FAILED, VariantStatus.FAILED)
                return None
            _store(connection, reply, "", MessageStatus.STREAMING, VariantStatus.STREAMING)
            return reply

        return write(self._engine, work)

    async def _generate(self, reply: _Reply, context: RunContext, streamed: list[str]) -> _Generated:
        """Streams the reply, publishing each token and flushing the text on time. `streamed` collects the
        tokens, so a failure handler sees what was already shown."""
        character, messages = await to_thread.run_sync(self._load_context, reply)
        split = split_current_message(messages)
        if split is None:
            raise JobFailedError(INTERNAL, _NOTHING_TO_ANSWER, retryable=False)
        history, current = split
        settings = self._settings
        formatter = formatter_for(settings.template)
        info = await self._runtime.info()
        prompt, prompt_tokens = await self._fit(
            formatter, character, reply.language, history, current, info.max_context_length - settings.max_length
        )
        request = TextRequest(
            prompt=prompt.text,
            max_context_length=info.max_context_length,
            max_length=settings.max_length,
            genkey=context.genkey,
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
        offset = 0
        finish: FinishReason | None = None
        last_flush = self._monotonic()
        async for chunk in self._runtime.stream(request):
            if chunk.final:
                finish = chunk.finish_reason
                continue
            streamed.append(chunk.token)
            delta = {"message_id": reply.message_id, "variant_id": reply.variant_id, "offset": offset}
            self._events.publish(MESSAGE_DELTA, {**delta, "text": chunk.token})
            offset += _utf16_length(chunk.token)
            if self._monotonic() - last_flush >= self._flush_interval_s:
                await to_thread.run_sync(self._flush, reply, "".join(streamed))
                last_flush = self._monotonic()
        if finish is None:
            raise RuntimeStreamDroppedError("the stream ended without its final chunk")
        status = MessageStatus.INTERRUPTED if finish == "aborted" else MessageStatus.COMPLETE
        return _Generated("".join(streamed), status, prompt_tokens)

    async def _fit(
        self,
        formatter: PromptFormatter,
        character: CharacterProfile,
        language: str,
        history: Sequence[Turn],
        current: str,
        budget: int,
    ) -> tuple[FormattedPrompt, int]:
        """Returns the prompt and its token count, with the oldest turns dropped until it fits `budget` tokens.

        Counts with the runtime's tokenizer, BOS included, never by estimate. A quarter of the remaining
        history goes at each step, so a long chat needs few counts. With no history left the prompt is sent
        as it is, and the runtime keeps the end of what does not fit.
        """
        kept = list(history)
        user = self._settings.user
        while True:
            prompt = formatter.chat(chat_parts(character, user, language, kept), current)
            tokens = await self._runtime.count_tokens(prompt.text, special=True)
            if tokens <= budget or not kept:
                return prompt, tokens
            del kept[: max(1, len(kept) // 4)]

    def _load_context(self, reply: _Reply) -> tuple[CharacterProfile, list[Message]]:
        def load(connection: Connection) -> tuple[CharacterProfile, list[Message]]:
            character = conversation_repository.find_profile(connection, reply.character_id)
            assert character is not None, "a conversation's character cannot be removed while the chat exists"
            messages = repository.prompt_messages(connection, reply.conversation_id, reply.seq, MAX_HISTORY_MESSAGES)
            return character, messages

        return read(self._engine, load)

    def _write(self, reply: _Reply, text: str, status: MessageStatus, variant_status: VariantStatus) -> Message:
        """Stores `text` and the statuses in one transaction and returns the message. Blocks: run it in a thread."""
        return write(self._engine, lambda connection: _store(connection, reply, text, status, variant_status))

    def _flush(self, reply: _Reply, text: str) -> None:
        self._write(reply, text, MessageStatus.STREAMING, VariantStatus.STREAMING)

    def _finish(self, reply: _Reply, generated: _Generated) -> Message:
        return self._write(reply, generated.text, generated.status, VariantStatus(generated.status.value))

    async def _cancelled(self, job: Job, reply: _Reply, text: str) -> None:
        if self._closing:
            # Shutdown: keep what was streamed and leave the reply `streaming`; the next start marks it interrupted
            # and ends its job, as after a crash.
            await to_thread.run_sync(self._flush, reply, text)
            return
        # The scheduler's only other cancellation is a lost lease, which it retries with LEASE_LOST.
        await self._settle(job, reply, text, LEASE_LOST, retry=job.attempt < job.max_attempts)

    async def _failed(self, job: Job, reply: _Reply, text: str, error: Exception) -> None:
        if isinstance(error, JobPreemptedError):
            # Preemption costs no attempt: the job always runs again.
            await self._settle(job, reply, text, LEASE_LOST, retry=True)
            return
        code, retryable = _failure(error)
        await self._settle(job, reply, text, code, retry=retryable and job.attempt < job.max_attempts)

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
