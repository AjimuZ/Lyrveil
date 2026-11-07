"""CharacterService: creates, reads and lists characters with their whole card.

Saving a changed card, versions, archive and purge belong to the rest of the Creator (P03.1b onward); this service holds
what creation and the chat need.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from lyrveil.domain.characters import CharacterSummary, StoredCharacter
from lyrveil.domain.errors import NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import characters as repository
from lyrveil.services.card_rules import validate_card

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

    def create(self, raw_card: Mapping[str, Any]) -> CharacterSummary:
        """Returns a new active character at version 1. `raw_card` holds `identity` and, when given, `traits`,
        `communication` and `visual`; only the first name and the age are required.

        Raises ValidationFailedError, with an Italian detail, for anything validate_card refuses: an age under 18 or
        over 120, an empty or over-long name, a value outside the closed lists, an unknown field, a slot tag the
        vocabulary lacks, or an outfit with a reserved key. Nothing is written in that case.
        """
        card = validate_card(raw_card)
        character_id = new_id()
        now = to_utc_text(self._clock())

        def work(connection: Any) -> CharacterSummary:
            repository.insert(connection, character_id=character_id, card=card, now=now)
            created = repository.get(connection, character_id)
            assert created is not None
            return created

        return write(self._engine, work)

    def get(self, character_id: str) -> StoredCharacter:
        """Returns the character with its whole card. Raises NotFoundError when no character has that id."""
        found = read(self._engine, lambda connection: repository.get_card(connection, character_id))
        if found is None:
            raise NotFoundError("Il personaggio non esiste.")
        return found

    def list_characters(self, status: str = "active", q: str | None = None) -> list[CharacterSummary]:
        """Returns the characters in `status`, newest update first. A non-blank `q` keeps those whose full name
        contains it, ignoring case; it is plain text, so `%` and `_` match themselves.

        Raises ValidationFailedError for a status outside STATUSES.
        """
        if status not in STATUSES:
            raise ValidationFailedError("Lo stato richiesto non è valido.")
        like = _like_pattern(q) if q is not None and q.strip() else None
        return read(self._engine, lambda connection: repository.list_all(connection, status, like))
