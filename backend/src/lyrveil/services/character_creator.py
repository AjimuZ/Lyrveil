"""CharacterCreatorService: assisted creation, the `character_draft` job from a description to a card to review.

docs/character-system.md (Assisted creation). `start_draft` queues the job; the service is also the job's handler,
which the scheduler runs under an llm lease. The handler generates CharacterDraft under the grammar of its schema,
validates it as a card, repairs it once (lyrveil.prompts.structured) and stores the card in the job's `result_json`. It
never writes a character: a draft becomes one only when the user saves it through `POST /characters` or
`PUT /characters/{id}`. Over an existing character the draft keeps what that character locks (`keep_locked`).
The content guard (ADR 0018) checks the description before the job is queued, and the draft's text as part of its
validation. No description, prompt or generated text is ever logged.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from anyio import to_thread
from pydantic import ValidationError
from sqlalchemy import Connection, Engine

from lyrveil.catalog import TextTemplate
from lyrveil.consistency.content_guard import check_draft_description
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.card_draft import CharacterDraft, card_as_draft, draft_to_raw_card, proposed_fields
from lyrveil.domain.card_locks import keep_locked
from lyrveil.domain.errors import ConflictError, ContentPolicyBlockedError, NotFoundError, ValidationFailedError
from lyrveil.domain.messages import JobRef
from lyrveil.jobs.handlers import INTERNAL, JobDone, JobFailedError, JobOutcome, RunContext
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import Job, JobQueue, QueueFullError
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import messages as message_repository
from lyrveil.ports.text_runtime import TextRuntime
from lyrveil.prompts import character_draft
from lyrveil.prompts.gbnf import schema_to_gbnf
from lyrveil.prompts.structured import (
    STRUCTURED_OUTPUT_INVALID,
    OutputRejectedError,
    StructuredOutputInvalidError,
    generate_structured,
)
from lyrveil.services.card_rules import describe_error, validate_card

MAX_DESCRIPTION_CHARS = 2000
# The job error code of a draft whose output and repair the content guard refused (docs/api.md, Errors).
CONTENT_POLICY_BLOCKED = ContentPolicyBlockedError.code
_BLANK = "Scrivi una descrizione del personaggio."
_TOO_LONG = f"La descrizione può avere al massimo {MAX_DESCRIPTION_CHARS} caratteri."
_NO_CHARACTER = "Il personaggio non esiste."
_QUEUE_FULL = "Troppe richieste in coda: riprova tra poco."
_CHARACTER_GONE = "Il personaggio non esiste più."

logger = get_logger(__name__)

# Generated once from the schema, so the grammar always follows the model.
DRAFT_GRAMMAR = schema_to_gbnf(CharacterDraft)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CharacterCreatorService:
    """Queues drafts on `engine` and, with a `runtime`, runs them as the JobHandler of `character_draft`.

    `runtime` must be used only under the run's lease, which the scheduler holds. `template` is the prompt family of
    the loaded text model. `clock` returns the aware current time; tests pass a fake one.
    """

    def __init__(
        self,
        engine: Engine,
        runtime: TextRuntime | None = None,
        *,
        template: TextTemplate = "gemma4",
        queue: JobQueue | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._template = template
        self._queue = queue or JobQueue(clock=clock)

    def start_draft(self, description: str, character_id: str | None = None) -> JobRef:
        """Queues a `character_draft` job for `description` and returns it. Nothing else is written.

        With `character_id` the draft rewrites that character's card and keeps what it locks. Raises
        ValidationFailedError for a blank description or one over MAX_DESCRIPTION_CHARS, ContentPolicyBlockedError
        when the description describes a minor (checked before anything is queued, so the model never reads it),
        NotFoundError for an unknown character, and ConflictError when the queue is full.
        """
        text_value = description.strip()
        if not text_value:
            raise ValidationFailedError(_BLANK)
        if len(text_value) > MAX_DESCRIPTION_CHARS:
            raise ValidationFailedError(_TOO_LONG)
        check_draft_description(text_value)

        def work(connection: Connection) -> JobRef:
            if character_id is not None and character_repository.get(connection, character_id) is None:
                raise NotFoundError(_NO_CHARACTER)
            job = self._queue.enqueue(
                connection, JobKind.CHARACTER_DRAFT, {"description": text_value}, character_id=character_id
            )
            return JobRef(
                id=job.id,
                kind=job.kind.value,
                status=job.status.value,
                queue_position=message_repository.queue_position(connection, job.id),
            )

        try:
            return write(self._engine, work)
        except QueueFullError:
            raise ConflictError(_QUEUE_FULL) from None

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        """Generates the draft of `job` and returns JobDone with `card`, the whole card to review, `proposed_fields`,
        the dotted paths the draft filled in, `character_id` and `attempts`.

        The description passes the content guard again before any generation, since a queued job may predate a list
        entry; a refusal raises JobFailedError CONTENT_POLICY_BLOCKED, not retried, and the model reads nothing. A
        draft whose text describes a minor is refused by the content guard like any invalid output, so the repair
        prompt carries the guard's reason. Raises JobFailedError, not retried, with the Italian reason of the last
        refusal when the first output and its repair are both refused: CONTENT_POLICY_BLOCKED when the content guard
        refused the last one, STRUCTURED_OUTPUT_INVALID otherwise (an age under 18 among them). Raises INTERNAL when
        the job has no description or its character no longer exists. A TextRuntimeError goes through for the
        scheduler to retry.
        """
        if self._runtime is None:
            raise RuntimeError("the draft handler needs a text runtime")
        description = job.payload.get("description")
        if not isinstance(description, str):
            raise JobFailedError(INTERNAL, None, retryable=False)
        try:
            check_draft_description(description)
        except ContentPolicyBlockedError as error:
            logger.info("draft_failed", job_id=job.id, error_code=CONTENT_POLICY_BLOCKED)
            raise JobFailedError(CONTENT_POLICY_BLOCKED, error.detail, retryable=False) from None
        current: CharacterCard | None = None
        if job.character_id is not None:
            current = await to_thread.run_sync(self._current_card, job.character_id)
            if current is None:
                raise JobFailedError(INTERNAL, _CHARACTER_GONE, retryable=False)
        base = current
        shown = None if base is None else card_as_draft(base).model_dump(mode="json")
        # The guard's details; a parse failure never reaches `validate`, so the last refusal is known by its detail.
        blocked: set[str] = set()

        def validate(raw: dict[str, Any]) -> CharacterCard:
            try:
                draft = CharacterDraft.model_validate(raw)
            except ValidationError as error:
                raise OutputRejectedError(describe_error(error)) from None
            try:
                card = validate_card(draft_to_raw_card(draft, base))
            except ValidationFailedError as error:
                raise OutputRejectedError(error.detail) from None
            except ContentPolicyBlockedError as error:
                blocked.add(error.detail)
                raise OutputRejectedError(error.detail) from None
            return card if base is None else keep_locked(base, card)

        try:
            result = await generate_structured(
                self._runtime,
                prompt=lambda repair: character_draft.render_prompt(self._template, description, shown, repair),
                grammar=DRAFT_GRAMMAR,
                validate=validate,
                genkey=context.genkey,
                max_length=character_draft.MAX_LENGTH,
                temperature=character_draft.TEMPERATURE,
            )
        except StructuredOutputInvalidError as invalid:
            code = CONTENT_POLICY_BLOCKED if invalid.detail in blocked else STRUCTURED_OUTPUT_INVALID
            logger.info("draft_failed", job_id=job.id, error_code=code)
            raise JobFailedError(code, invalid.detail, retryable=False) from None
        card = result.value
        logger.info("draft_finished", job_id=job.id, attempt_count=result.attempts)
        return JobDone(
            {
                "card": card.model_dump(mode="json"),
                "proposed_fields": proposed_fields(card, current),
                "character_id": job.character_id,
                "attempts": result.attempts,
            }
        )

    def _current_card(self, character_id: str) -> CharacterCard | None:
        found = read(self._engine, lambda connection: character_repository.get_card(connection, character_id))
        return None if found is None else found.card
