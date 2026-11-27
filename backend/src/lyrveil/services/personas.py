"""PersonaService: creates, lists, edits, archives, deletes and defaults user personas (ADR 0048).

A persona is the user's identity in a conversation. The conversation stores which persona it uses and that never
changes (ConversationService); this service owns the persona rows and the rules about them: every text passes the
content guard of ADR 0037, one persona is the default, a used persona can only be archived, and the default is
neither archived nor deleted until another persona is the default.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from lyrveil.consistency.content_guard import find_violation
from lyrveil.domain.errors import ConflictError, ContentPolicyBlockedError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.personas import (
    GRAMMATICAL_GENDERS,
    MAX_ABOUT_CHARS,
    MAX_AGE,
    MAX_APPEARANCE_CHARS,
    MAX_NAME_CHARS,
    MIN_AGE,
    Persona,
    PersonaStatus,
)
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import personas as repository

NO_PERSONA = "Persona non trovata."
# The text fields, with the Italian name the refusals use and the longest value each takes.
_TEXT_FIELDS = (
    ("name", "nome", MAX_NAME_CHARS),
    ("appearance", "aspetto", MAX_APPEARANCE_CHARS),
    ("about", "chi sono", MAX_ABOUT_CHARS),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def guard_text(value: str, label: str) -> None:
    """Raises ContentPolicyBlockedError, naming `label` and never quoting `value`, when the text describes a minor or
    other content the safeguard of ADR 0018 refuses. Persona text is read by the model in every conversation that uses
    the persona, as a character card is."""
    if find_violation(value) is not None:
        raise ContentPolicyBlockedError(
            f"Il campo «{label}» descrive un minore o un contenuto che l'app non consente. Modifica il testo."
        )


def _checked(fields: dict[str, Any]) -> dict[str, object]:
    """Returns the editable `fields` with the text trimmed.

    Raises ValidationFailedError for a blank or over-long text, an unknown gender or an age that is not NULL or 18 to
    120, and ContentPolicyBlockedError when a text fails the content guard. A missing gender is `non_indicato`.
    """
    clean: dict[str, object] = {}
    for name, label, limit in _TEXT_FIELDS:
        value = (fields.get(name) or "").strip()
        if len(value) > limit:
            raise ValidationFailedError(f"Il campo «{label}» supera {limit} caratteri.")
        guard_text(value, label)
        clean[name] = value
    if not clean["name"]:
        raise ValidationFailedError("Il nome non può essere vuoto.")
    gender = fields.get("grammatical_gender") or "non_indicato"
    if gender not in GRAMMATICAL_GENDERS:
        raise ValidationFailedError("Il genere grammaticale non è valido.")
    clean["grammatical_gender"] = gender
    age = fields.get("age")
    if age is not None and (isinstance(age, bool) or not isinstance(age, int) or not MIN_AGE <= age <= MAX_AGE):
        raise ValidationFailedError(f"L'età deve essere vuota oppure tra {MIN_AGE} e {MAX_AGE} anni.")
    clean["age"] = age
    return clean


class PersonaService:
    """Reads and changes the personas of `engine`. `clock` returns the aware current time; tests pass a fake one."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def create(self, fields: dict[str, Any]) -> Persona:
        """Returns a new active persona that is not the default. `fields` holds `name` (required) and optionally
        `grammatical_gender`, `age`, `appearance` and `about`. Raises what `_checked` raises."""
        clean = _checked(fields)
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Persona:
            persona_id = new_id()
            repository.insert(connection, persona_id, clean, now)
            return self._current(connection, persona_id)

        return write(self._engine, work)

    def list_personas(self, status: PersonaStatus = PersonaStatus.ACTIVE) -> list[Persona]:
        """Returns the personas in `status`: the default first, then by name."""
        return read(self._engine, lambda connection: repository.list_all(connection, status))

    def get(self, persona_id: str) -> Persona:
        """Returns the persona in any status; raises NotFoundError when none has that id."""
        found = read(self._engine, lambda connection: repository.get(connection, persona_id))
        if found is None:
            raise NotFoundError(NO_PERSONA)
        return found

    def update(self, persona_id: str, fields: dict[str, Any]) -> Persona:
        """Replaces every editable field (PUT) and returns the persona. The change reaches the conversations that use
        it from their next reply. Raises NotFoundError for an unknown persona, and what `_checked` raises."""
        clean = _checked(fields)
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Persona:
            self._current(connection, persona_id)
            repository.update(connection, persona_id, now, **clean)
            return self._current(connection, persona_id)

        return write(self._engine, work)

    def set_default(self, persona_id: str) -> Persona:
        """Makes the persona the default one and returns it. Making the default persona the default again changes
        nothing. Raises NotFoundError for an unknown persona and ConflictError for an archived one, which must be
        unarchived first."""
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Persona:
            current = self._current(connection, persona_id)
            if current.status is PersonaStatus.ARCHIVED:
                raise ConflictError("Una persona archiviata non può diventare quella predefinita.")
            if not current.is_default:
                repository.make_default(connection, persona_id, now)
            return self._current(connection, persona_id)

        return write(self._engine, work)

    def set_archived(self, persona_id: str, archived: bool) -> Persona:
        """Archives or restores the persona and returns it. An archived persona leaves the new-chat picker and starts no
        conversation, and keeps the conversations that use it. Raises NotFoundError for an unknown persona and
        ConflictError when archiving the default persona."""
        now = to_utc_text(self._clock())
        status = PersonaStatus.ARCHIVED if archived else PersonaStatus.ACTIVE

        def work(connection: Any) -> Persona:
            current = self._current(connection, persona_id)
            if archived and current.is_default:
                raise ConflictError("La persona predefinita non si archivia: scegline prima un'altra.")
            if current.status is not status:
                repository.update(connection, persona_id, now, status=status.value)
            return self._current(connection, persona_id)

        return write(self._engine, work)

    def delete(self, persona_id: str) -> None:
        """Removes a persona that no conversation uses. Scenarios that preselect it lose the link. Raises NotFoundError
        for an unknown persona and ConflictError while a conversation uses it, which can then only be archived, or while
        it is the default."""

        def work(connection: Any) -> None:
            current = self._current(connection, persona_id)
            if current.is_default:
                raise ConflictError("La persona predefinita non si elimina: scegline prima un'altra.")
            if current.conversation_count:
                raise ConflictError("Una conversazione usa questa persona: puoi solo archiviarla.")
            repository.delete(connection, persona_id)

        write(self._engine, work)

    @staticmethod
    def _current(connection: Any, persona_id: str) -> Persona:
        found = repository.get(connection, persona_id)
        if found is None:
            raise NotFoundError(NO_PERSONA)
        return found
