"""SummaryService: the `summary_checkpoint` job, which writes the progressive summaries of a conversation, and the
trigger that queues it (docs/memory-engine.md, Progressive summaries).

A run does one piece of work, in this order: re-summarize the range its payload names (a new version of a level-0
checkpoint), summarize the next chunk of messages after `last_summarized_seq` (level 0), or merge the oldest four of six
consecutive checkpoints of a level into one of the next level (a rollup). It generates `SummaryResult` under the grammar
of its schema with the loaded text model, validates it and repairs it once (lyrveil.prompts.structured). Then one
transaction inserts the checkpoint and, for a chunk, moves the cursor. Originals and the rows a rollup merges are never
changed; a replaced version is only marked `superseded`. Invalid output, a runtime failure or an interruption writes
nothing; the job retries with the backoff of its kind.

The job needs the `llm` mode and is background work, so it never runs in image mode and never causes a mode change
(docs/gpu-orchestration.md). No message, summary or model output is ever logged.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from anyio import to_thread
from pydantic import ValidationError
from sqlalchemy import Connection, Engine

from lyrveil.catalog import TextTemplate
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.conversations import ConversationStatus
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import Author
from lyrveil.domain.summaries import SummaryResult
from lyrveil.jobs.handlers import INTERNAL, JobDone, JobFailedError, JobOutcome, RunContext
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import Job, JobQueue, QueueFullError
from lyrveil.jobs.scheduler import write
from lyrveil.logs import get_logger
from lyrveil.memory.summaries import (
    Chunk,
    Rollup,
    SummarySettings,
    clean_source_seqs,
    next_chunk,
    next_rollup,
)
from lyrveil.memory.window import WindowMessage, readable_before_pending, window_hash
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import memory_extraction as message_repository
from lyrveil.persistence import summaries as repository
from lyrveil.ports.text_runtime import TextRuntime
from lyrveil.prompts import summary
from lyrveil.prompts.extraction import PromptMessage
from lyrveil.prompts.gbnf import schema_to_gbnf
from lyrveil.prompts.structured import (
    STRUCTURED_OUTPUT_INVALID,
    OutputRejectedError,
    StructuredOutputInvalidError,
    generate_structured,
)
from lyrveil.prompts.summary import RolledCheckpoint, SummaryPrompt

# Generated once from the schema, so the grammar always follows the model.
SUMMARY_GRAMMAR = schema_to_gbnf(SummaryResult)
PROMPT_VERSION = summary.TEMPLATE_VERSION
# Messages read after the cursor to find a chunk: the most one holds (settings allow 100) and room for passed-over ones.
SCAN_LIMIT = 400
_LIST_FIELDS = ("events", "decisions", "relationship_changes", "unresolved", "key_facts")
_NOT_A_RESULT = "La risposta non è un riassunto valido."

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


def validate_result(raw: dict[str, Any]) -> SummaryResult:
    """Returns the parsed output as a SummaryResult. Raises OutputRejectedError with an Italian detail when it does not
    match the schema or its text is blank."""
    try:
        result = SummaryResult.model_validate(raw)
    except ValidationError as error:
        raise OutputRejectedError(_describe(error)) from None
    if not result.text.strip():
        raise OutputRejectedError("Il campo «text» è vuoto.")
    return result


def _chunk_due(connection: Connection, conversation_id: str, settings: SummarySettings) -> Chunk | None:
    cursor = repository.cursor(connection, conversation_id)
    after = message_repository.messages_after(connection, conversation_id, cursor, limit=SCAN_LIMIT)
    tail = repository.tail_start(connection, conversation_id, settings.recent_tail_messages)
    return next_chunk(after, cursor, tail, settings)


def queue_summary_if_due(
    connection: Connection,
    queue: JobQueue,
    conversation_id: str,
    settings: SummarySettings,
    *,
    exclude_job_id: str | None = None,
) -> Job | None:
    """Queues a `summary_checkpoint` job for the conversation in the caller's write transaction when a chunk or a rollup
    is due, and returns it; otherwise returns None and writes nothing.

    Never queues for a missing or deleted conversation, nor while another summary job of the conversation is queued or
    running (`exclude_job_id` is the run that asks, which does not count). A full queue queues nothing and raises
    nothing: the next trigger tries again.
    """
    conversation = conversation_repository.get(connection, conversation_id)
    if conversation is None or conversation.status is ConversationStatus.DELETED:
        return None
    if repository.has_active_summary_job(connection, conversation_id, exclude_job_id):
        return None
    due = _chunk_due(connection, conversation_id, settings) is not None
    if not due and next_rollup(repository.checkpoints(connection, conversation_id)) is None:
        return None
    try:
        return queue.enqueue(
            connection,
            JobKind.SUMMARY_CHECKPOINT,
            {},
            conversation_id=conversation_id,
            character_id=conversation.character_id,
        )
    except QueueFullError:
        logger.info("summary_not_queued", conversation_id=conversation_id, state="queue_full")
        return None


@dataclass(frozen=True)
class _Skipped:
    """A run that needs no generation. `reason` is `conversation_deleted` or `nothing_to_summarize`."""

    reason: str


@dataclass(frozen=True)
class _Prepared:
    """A started run: what it covers and the prompt that asks for it. `messages` is set for a level-0 range and
    `rollup` for a rollup. `resummarize` tells that the range replaces an earlier version."""

    conversation_id: str
    level: int
    from_seq: int
    to_seq: int
    source_hash: str
    prompt: SummaryPrompt
    messages: tuple[WindowMessage, ...] = ()
    rollup: Rollup | None = None
    resummarize: bool = False


class SummaryService:
    """Runs one summary step per call, as the JobHandler of `summary_checkpoint` (lyrveil.jobs.handlers).

    `runtime` must be used only under the run's lease, which the scheduler holds. `template` is the prompt family of the
    loaded text model and `user` names the user in the prompt. `settings` gives the chunk size and the recent tail, and
    the trigger a finished run checks again. `clock` returns the aware current time; tests pass a fake one.
    """

    def __init__(
        self,
        engine: Engine,
        runtime: TextRuntime | None = None,
        *,
        template: TextTemplate = "gemma4",
        user: UserProfile | None = None,
        settings: SummarySettings | None = None,
        queue: JobQueue | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._template = template
        self._user = user or UserProfile()
        self._settings = settings or SummarySettings()
        self._queue = queue or JobQueue(clock=clock)
        self._clock = clock

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        """Writes the next checkpoint of the job's conversation and returns JobDone with `status` and what it stored.

        The job payload may name a level-0 range to summarize again, `{"from_seq": int, "to_seq": int}`; otherwise the
        run takes the next chunk, or else the next rollup. `status` is `completed` (with `kind` `chunk`, `rollup` or
        `resummarize`, `checkpoint_id`, `level`, `from_seq`, `to_seq`, `version`, `token_count`, `attempts`),
        `nothing_to_summarize`, `conversation_deleted`, or `superseded` (the covered messages or checkpoints changed
        while the model ran: nothing is written and the trigger is checked again). Raises JobFailedError
        STRUCTURED_OUTPUT_INVALID, retryable, when the output and its repair are both refused; a TextRuntimeError goes
        through for the scheduler to retry. Either way nothing is written. Raises INTERNAL, not retryable, for a job
        without a conversation or with a payload range that is not valid.
        """
        if self._runtime is None:
            raise RuntimeError("the summary handler needs a text runtime")
        if job.conversation_id is None:
            raise JobFailedError(INTERNAL, None, retryable=False)
        named = _named_range(job.payload)
        prepared = await to_thread.run_sync(self._prepare, job.conversation_id, named)
        if isinstance(prepared, _Skipped):
            logger.info("summary_skipped", job_id=job.id, conversation_id=job.conversation_id, state=prepared.reason)
            return JobDone({"status": prepared.reason})
        shown = prepared.prompt
        try:
            result = await generate_structured(
                self._runtime,
                prompt=lambda repair: summary.render_prompt(self._template, shown, repair),
                grammar=SUMMARY_GRAMMAR,
                validate=validate_result,
                genkey=context.genkey,
                max_length=summary.MAX_LENGTH,
                temperature=summary.TEMPERATURE,
            )
            tokens = await self._runtime.count_tokens(result.value.text.strip())
        except StructuredOutputInvalidError as invalid:
            logger.info(
                "summary_failed",
                job_id=job.id,
                conversation_id=prepared.conversation_id,
                error_code=STRUCTURED_OUTPUT_INVALID,
            )
            raise JobFailedError(STRUCTURED_OUTPUT_INVALID, invalid.detail, retryable=True) from None
        outcome = await to_thread.run_sync(self._commit, job, prepared, result.value, tokens, result.attempts)
        logger.info(
            "summary_finished",
            job_id=job.id,
            conversation_id=prepared.conversation_id,
            state=str(outcome["status"]),
            level=prepared.level,
            attempt_count=result.attempts,
        )
        return JobDone(outcome)

    def _prepare(self, conversation_id: str, named: tuple[int, int] | None) -> _Prepared | _Skipped:
        """Decides what the run covers and builds its prompt, in one read-write transaction: the named range, the next
        chunk or the next rollup, in that order."""

        def work(connection: Connection) -> _Prepared | _Skipped:
            conversation = conversation_repository.get(connection, conversation_id)
            if conversation is None or conversation.status is ConversationStatus.DELETED:
                return _Skipped("conversation_deleted")
            character = conversation_repository.find_profile(connection, conversation.character_id)
            assert character is not None, "a conversation's character cannot be removed while the chat exists"
            language = conversation.effective_language
            names = {Author.USER: self._user.display_name, Author.CHARACTER: character.first_name}

            def prompt(**parts: Any) -> SummaryPrompt:
                return SummaryPrompt(self._user.display_name, character.full_name, language, **parts)

            if named is not None:
                found = self._range_messages(connection, conversation_id, *named)
                if not found:
                    return _Skipped("nothing_to_summarize")
                return self._for_messages(conversation_id, named, found, names, prompt, resummarize=True)
            chunk = _chunk_due(connection, conversation_id, self._settings)
            if chunk is not None:
                span = (chunk.from_seq, chunk.to_seq)
                return self._for_messages(conversation_id, span, chunk.messages, names, prompt, resummarize=False)
            rollup = next_rollup(repository.checkpoints(connection, conversation_id))
            if rollup is None:
                return _Skipped("nothing_to_summarize")
            stored = repository.stored_summaries(connection, [source.id for source in rollup.sources])
            parts = [
                RolledCheckpoint(
                    item.checkpoint.from_seq,
                    item.checkpoint.to_seq,
                    item.text,
                    {name: [str(entry) for entry in item.content.get(name, [])] for name in _LIST_FIELDS},
                )
                for item in stored
            ]
            return _Prepared(
                conversation_id=conversation_id,
                level=rollup.level,
                from_seq=rollup.from_seq,
                to_seq=rollup.to_seq,
                source_hash=rollup.hash,
                prompt=prompt(parts=parts),
                rollup=rollup,
            )

        return write(self._engine, work)

    @staticmethod
    def _range_messages(
        connection: Connection, conversation_id: str, first: int, last: int
    ) -> tuple[WindowMessage, ...]:
        """Returns the readable messages from `first` to `last`, oldest first, up to the first pending reply."""
        found = message_repository.messages_after(
            connection, conversation_id, first - 1, limit=SCAN_LIMIT, up_to_seq=last
        )
        return tuple(readable_before_pending(found))

    @staticmethod
    def _for_messages(
        conversation_id: str,
        span: tuple[int, int],
        messages: Sequence[WindowMessage],
        names: Mapping[Author, str],
        prompt: Callable[..., SummaryPrompt],
        *,
        resummarize: bool,
    ) -> _Prepared:
        """Returns the run for a level-0 range `span` (first and last seq) whose readable messages are `messages`."""
        shown = [PromptMessage(m.seq, names.get(m.author, m.author.value), m.content) for m in messages]
        return _Prepared(
            conversation_id=conversation_id,
            level=0,
            from_seq=span[0],
            to_seq=span[1],
            source_hash=window_hash(messages),
            prompt=prompt(messages=shown),
            messages=tuple(messages),
            resummarize=resummarize,
        )

    def _commit(
        self, job: Job, prepared: _Prepared, result: SummaryResult, tokens: int, attempts: int
    ) -> dict[str, Any]:
        """Writes the checkpoint, and for a new chunk the cursor, in one transaction and returns the job result.

        What the run covered is read again first: if the cursor moved, a message of the range changed or the
        checkpoints a rollup merges are no longer the ones due while the model ran, nothing is written and the trigger
        is checked again.
        """
        now = to_utc_text(self._clock())
        conversation_id = prepared.conversation_id

        def work(connection: Connection) -> dict[str, Any]:
            if not self._still_valid(connection, prepared):
                queue_summary_if_due(connection, self._queue, conversation_id, self._settings, exclude_job_id=job.id)
                return {"status": "superseded", "level": prepared.level, "from_seq": prepared.from_seq}
            level, first, last = prepared.level, prepared.from_seq, prepared.to_seq
            version = repository.next_version(connection, conversation_id, level, first, last)
            if prepared.resummarize:
                repository.supersede(connection, conversation_id, level, first, last)
            content = {name: list(getattr(result, name)) for name in _LIST_FIELDS}
            content["source_seqs"] = clean_source_seqs(result.source_seqs, prepared.from_seq, prepared.to_seq)
            checkpoint_id = new_id()
            repository.insert_checkpoint(
                connection,
                repository.NewCheckpoint(
                    id=checkpoint_id,
                    conversation_id=conversation_id,
                    level=prepared.level,
                    from_seq=prepared.from_seq,
                    to_seq=prepared.to_seq,
                    version=version,
                    content=content,
                    text=result.text.strip(),
                    source_hash=prepared.source_hash,
                    prompt_version=PROMPT_VERSION,
                    token_count=tokens,
                ),
                now,
            )
            kind = "rollup" if prepared.rollup is not None else "resummarize" if prepared.resummarize else "chunk"
            if kind == "chunk":
                repository.advance_cursor(connection, conversation_id, prepared.to_seq)
            queue_summary_if_due(connection, self._queue, conversation_id, self._settings, exclude_job_id=job.id)
            return {
                "status": "completed",
                "kind": kind,
                "checkpoint_id": checkpoint_id,
                "level": prepared.level,
                "from_seq": prepared.from_seq,
                "to_seq": prepared.to_seq,
                "version": version,
                "token_count": tokens,
                "attempts": attempts,
            }

        return write(self._engine, work)

    def _still_valid(self, connection: Connection, prepared: _Prepared) -> bool:
        """Tells whether what the run covered is still what it read before generating."""
        conversation_id = prepared.conversation_id
        if prepared.rollup is not None:
            due = next_rollup(repository.checkpoints(connection, conversation_id))
            return due is not None and [c.id for c in due.sources] == [c.id for c in prepared.rollup.sources]
        if not prepared.resummarize and repository.cursor(connection, conversation_id) != prepared.from_seq - 1:
            return False
        current = self._range_messages(connection, conversation_id, prepared.from_seq, prepared.to_seq)
        return bool(current) and window_hash(current) == prepared.source_hash


def _named_range(payload: Mapping[str, Any]) -> tuple[int, int] | None:
    """Returns the level-0 range a job payload names, or None when it names none. Raises JobFailedError INTERNAL, not
    retryable, for a range that is not two integers with `1 <= from_seq <= to_seq`."""
    if "from_seq" not in payload and "to_seq" not in payload:
        return None
    first, last = payload.get("from_seq"), payload.get("to_seq")
    if type(first) is not int or type(last) is not int or not 1 <= first <= last:
        raise JobFailedError(INTERNAL, None, retryable=False)
    return first, last
