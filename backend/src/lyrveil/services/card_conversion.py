"""CardConversionService: the optional `card_conversion` job of a card import (ADR 0047; docs/character-system.md,
Character cards).

`start` queues the job for a ready import; the service is also the job's handler, which the scheduler runs under an llm
lease. The handler reads the stored card as data, refuses it with `CARD_TOO_LONG` before any generation when it leaves
too little room in the context, and otherwise has the model write a `CharacterDraft` in the character's language
(Italian) under the grammar, validation and single repair of assisted creation (ADR 0013). The result is a draft for the
Creator whose fields are marked **proposto**. The age the model wrote is never used: the draft keeps the age of the
direct mapping, which is empty unless the card states an adult age. The service never writes a character and never
changes the import except for its pointer to the newest job. No card text, prompt or generated text is ever logged.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from anyio import to_thread
from sqlalchemy import Connection, Engine

from lyrveil.catalog import TextTemplate
from lyrveil.domain.card import CharacterCard, LanguageCode
from lyrveil.domain.card_draft import card_as_draft, proposed_fields
from lyrveil.domain.card_imports import SAVED
from lyrveil.domain.card_mapping import ImportedDraft
from lyrveil.domain.card_placeholders import expand_macros
from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.domain.messages import JobRef
from lyrveil.jobs.handlers import INTERNAL, JobDone, JobFailedError, JobOutcome, RunContext
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import ACTIVE_STATUSES, Job, JobQueue, QueueFullError
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.persistence import card_imports as repository
from lyrveil.persistence import messages as message_repository
from lyrveil.ports.text_runtime import TextRuntime
from lyrveil.prompts import card_conversion
from lyrveil.prompts.card_conversion import CardText
from lyrveil.prompts.structured import STRUCTURED_OUTPUT_INVALID, StructuredOutputInvalidError, generate_structured
from lyrveil.services.card_import import card_data
from lyrveil.services.character_creator import CONTENT_POLICY_BLOCKED, DRAFT_GRAMMAR, DraftBlockedError, check_draft

# The job error code of a card that leaves too little room for the conversion (docs/api.md, Errors).
CARD_TOO_LONG = "CARD_TOO_LONG"
# Until `characters.default_language` exists in the backend, the conversion writes Italian (ADR 0047).
DEFAULT_LANGUAGE: LanguageCode = "it"

_NO_IMPORT = "L'importazione non esiste o è scaduta."
_SAVED_IMPORT = "L'importazione è già stata salvata come personaggio."
_RUNNING = "La struttura con il modello è già in corso per questa card."
_QUEUE_FULL = "Troppe richieste in coda: riprova tra poco."
_GONE = "L'importazione non esiste più."
_TOO_LONG = (
    "Il testo della card è troppo lungo per il contesto del modello attivo: apri la card con la mappatura diretta "
    "o accorcia il testo."
)

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _expanded(value: str) -> str:
    """Returns a card text with its line ends made `\\n` and the macros of ADR 0047 applied, as the direct mapping."""
    return expand_macros(value.replace("\r\n", "\n").replace("\r", "\n"))[0].strip()


class CardConversionService:
    """Queues conversions on `engine` and, with a `runtime`, runs them as the JobHandler of `card_conversion`.

    `runtime` must be used only under the run's lease, which the scheduler holds. `template` is the prompt family of
    the loaded text model. `clock` returns the aware current time; tests pass a fake one.
    """

    def __init__(
        self,
        engine: Engine,
        runtime: TextRuntime | None = None,
        *,
        template: TextTemplate = "mistral_v3_tekken",
        queue: JobQueue | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._template = template
        self._clock = clock
        self._queue = queue or JobQueue(clock=clock)

    def start(self, import_id: str) -> JobRef:
        """Queues a `card_conversion` job for the import and returns it, and points the import at it. The card is not
        read here: the token count needs the runtime, so `CARD_TOO_LONG` comes from the job.

        A conversion that ended, failed or was cancelled can be started again; the new job replaces the pointer.
        Raises NotFoundError for an unknown import and for one that expired unsaved, ConflictError for one that was
        saved, while a conversion of the same import is queued or running, and when the queue is full.
        """

        def work(connection: Connection) -> JobRef:
            stored = repository.get(connection, import_id)
            if stored is None or (stored.status != SAVED and stored.expires_at <= to_utc_text(self._clock())):
                raise NotFoundError(_NO_IMPORT)
            if stored.status == SAVED:
                raise ConflictError(_SAVED_IMPORT)
            if stored.conversion_job_id is not None:
                previous = self._queue.get(connection, stored.conversion_job_id)
                if previous is not None and previous.status in ACTIVE_STATUSES:
                    raise ConflictError(_RUNNING)
            job = self._queue.enqueue(connection, JobKind.CARD_CONVERSION, {"import_id": import_id})
            repository.set_conversion_job(connection, import_id, job.id)
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
        """Converts the card of `job` and returns JobDone with `draft`, an ImportedDraft as a dict whose
        `identity.age` is the direct mapping's, `proposed_fields`, the dotted paths the model filled in (never
        `identity.age`), `import_id` and `attempts`.

        Counts the first prompt with the runtime and raises JobFailedError CARD_TOO_LONG, not retried, when that count
        plus the draft's `max_length` exceeds the context: nothing is generated then. Raises JobFailedError, not
        retried, with the Italian reason of the last refusal when the first output and its repair are both refused:
        CONTENT_POLICY_BLOCKED when the content guard refused the last one, STRUCTURED_OUTPUT_INVALID otherwise. Raises
        INTERNAL when the job has no import or the import is gone. A TextRuntimeError goes through for the scheduler to
        retry.
        """
        if self._runtime is None:
            raise RuntimeError("the conversion handler needs a text runtime")
        import_id = job.payload.get("import_id")
        if not isinstance(import_id, str):
            raise JobFailedError(INTERNAL, None, retryable=False)
        loaded = await to_thread.run_sync(self._load, import_id)
        if loaded is None:
            raise JobFailedError(INTERNAL, _GONE, retryable=False)
        card, age = loaded
        language = DEFAULT_LANGUAGE
        info = await self._runtime.info()
        used = await self._runtime.count_tokens(
            card_conversion.render_prompt(self._template, card, language, None), special=True
        )
        if used + card_conversion.MAX_LENGTH > info.max_context_length:
            logger.info("card_conversion_failed", job_id=job.id, error_code=CARD_TOO_LONG)
            raise JobFailedError(CARD_TOO_LONG, _TOO_LONG, retryable=False)
        # The guard's details; a parse failure never reaches `validate`, so the last refusal is known by its detail.
        blocked: set[str] = set()

        def validate(raw: dict[str, Any]) -> CharacterCard:
            try:
                return check_draft(raw, None)
            except DraftBlockedError as error:
                blocked.add(error.detail)
                raise

        try:
            result = await generate_structured(
                self._runtime,
                prompt=lambda repair: card_conversion.render_prompt(self._template, card, language, repair),
                grammar=DRAFT_GRAMMAR,
                validate=validate,
                genkey=context.genkey,
                max_length=card_conversion.MAX_LENGTH,
                temperature=card_conversion.TEMPERATURE,
            )
        except StructuredOutputInvalidError as invalid:
            code = CONTENT_POLICY_BLOCKED if invalid.detail in blocked else STRUCTURED_OUTPUT_INVALID
            logger.info("card_conversion_failed", job_id=job.id, error_code=code)
            raise JobFailedError(code, invalid.detail, retryable=False) from None
        card_model = result.value
        proposed = card_as_draft(card_model).model_dump(mode="json")
        # The model's age is dropped here, so no later step can use it: only an age the card states survives.
        proposed["identity"]["age"] = age
        proposed["identity"]["primary_language"] = language
        draft = ImportedDraft.model_validate(proposed)
        logger.info("card_conversion_finished", job_id=job.id, attempt_count=result.attempts)
        return JobDone(
            {
                "draft": draft.model_dump(mode="json"),
                "proposed_fields": [path for path in proposed_fields(card_model, None) if path != "identity.age"],
                "import_id": import_id,
                "attempts": result.attempts,
            }
        )

    def _load(self, import_id: str) -> tuple[CardText, int | None] | None:
        """Returns the card text to convert and the age of the direct mapping (None when the card states none), or None
        when the import no longer exists."""

        def work(connection: Connection) -> tuple[CardText, int | None] | None:
            stored = repository.get(connection, import_id)
            if stored is None:
                return None
            data = card_data(json.loads(stored.source_json))
            text = CardText(
                name=_expanded(data.name),
                description=_expanded(data.description),
                personality=_expanded(data.personality),
                scenario=_expanded(data.scenario),
                first_message=_expanded(data.first_mes),
                examples=_expanded(data.mes_example),
            )
            return text, _direct_age(stored.direct_draft_json)

        return read(self._engine, work)


def _direct_age(direct_draft_json: str | None) -> int | None:
    """Returns `draft.identity.age` of the stored direct mapping; None for a row stored before the mapping existed."""
    if direct_draft_json is None:
        return None
    age = json.loads(direct_draft_json)["draft"]["identity"]["age"]
    return age if isinstance(age, int) and not isinstance(age, bool) else None
