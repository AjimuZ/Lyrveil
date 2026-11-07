"""CharacterService: creates and lists the characters of the minimal form of P02.6a.

The full card, versions, archive and purge belong to the Creator (P03); this service only holds what a chat needs.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from lyrveil.context.minimal import LANGUAGE_NAMES
from lyrveil.domain.characters import CharacterSummary
from lyrveil.domain.errors import ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import characters as repository

MIN_AGE = 18  # ADR 0018: every character is an adult.
MAX_AGE = 120
MAX_NAME_LENGTH = 80  # The CHECK constraint of the table.
GENDERS = frozenset({"femminile", "maschile", "non_indicato"})
STATUSES = frozenset({"active", "archived"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _like_pattern(text_value: str) -> str:
    escaped = text_value.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class CharacterService:
    """Reads and writes characters of `engine`. `clock` returns the aware current time; tests pass a fake one."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def create(
        self,
        *,
        first_name: str,
        last_name: str | None,
        age: int,
        grammatical_gender: str = "non_indicato",
        primary_language: str = "it",
    ) -> CharacterSummary:
        """Returns a new active character. Names are trimmed and a blank last name is stored as none.

        Raises ValidationFailedError, with an Italian detail, for an empty or over-long first name, an age under
        MIN_AGE or over MAX_AGE, a gender outside GENDERS and a language outside the codes the prompt knows.
        """
        first = first_name.strip()
        last = (last_name or "").strip() or None
        if not first or len(first) > MAX_NAME_LENGTH:
            raise ValidationFailedError("Il nome deve avere da 1 a 80 caratteri.")
        if last is not None and len(last) > MAX_NAME_LENGTH:
            raise ValidationFailedError("Il cognome può avere al massimo 80 caratteri.")
        if age < MIN_AGE:
            raise ValidationFailedError("L'età deve essere almeno 18.")
        if age > MAX_AGE:
            raise ValidationFailedError("L'età deve essere al massimo 120.")
        if grammatical_gender not in GENDERS:
            raise ValidationFailedError("Il genere grammaticale non è valido.")
        if primary_language not in LANGUAGE_NAMES:
            raise ValidationFailedError("La lingua non è supportata.")
        character_id = new_id()
        now = to_utc_text(self._clock())

        def work(connection: Any) -> CharacterSummary:
            repository.insert(
                connection,
                character_id=character_id,
                first_name=first,
                last_name=last,
                age=age,
                grammatical_gender=grammatical_gender,
                primary_language=primary_language,
                now=now,
            )
            created = repository.get(connection, character_id)
            assert created is not None
            return created

        return write(self._engine, work)

    def list_characters(self, status: str = "active", q: str | None = None) -> list[CharacterSummary]:
        """Returns the characters in `status`, newest update first. A non-blank `q` keeps those whose full name
        contains it, ignoring case; it is plain text, so `%` and `_` match themselves.

        Raises ValidationFailedError for a status outside STATUSES.
        """
        if status not in STATUSES:
            raise ValidationFailedError("Lo stato richiesto non è valido.")
        like = _like_pattern(q) if q is not None and q.strip() else None
        return read(self._engine, lambda connection: repository.list_all(connection, status, like))
