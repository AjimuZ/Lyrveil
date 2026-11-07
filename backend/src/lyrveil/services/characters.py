"""CharacterService: creates, reads, saves and lists characters with their whole card, and keeps their versions.

A saved change appends a version, restoring appends one too, and history is never rewritten. Archive and purge belong to
the rest of the Creator (P03); this service holds what creation, editing and the chat need.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.card_diff import FieldChange, diff_cards
from lyrveil.domain.card_locks import keep_locked
from lyrveil.domain.characters import CharacterSummary, StoredCharacter, StoredVersion, VersionSummary
from lyrveil.domain.errors import NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import characters as repository
from lyrveil.services.card_rules import validate_card

STATUSES = frozenset({"active", "archived"})
_SECTION_NAMES = {"identity": "identità", "traits": "tratti", "communication": "comunicazione", "visual": "aspetto"}
_NO_CHARACTER = "Il personaggio non esiste."
_NO_VERSION = "La versione non esiste."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _like_pattern(text_value: str) -> str:
    escaped = text_value.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _change_summary(changes: list[FieldChange]) -> str:
    """Returns the Italian summary of a version: the card sections that changed, in card order."""
    sections = list(dict.fromkeys(change.field.split(".")[0] for change in changes))
    return "Modificati: " + ", ".join(_SECTION_NAMES.get(section, section) for section in sections)


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
            repository.insert_version(
                connection,
                character_id=character_id,
                version_no=1,
                card=card,
                change_summary="Creazione",
                actor="user",
                now=now,
            )
            created = repository.get(connection, character_id)
            assert created is not None
            return created

        return write(self._engine, work)

    def get(self, character_id: str) -> StoredCharacter:
        """Returns the character with its whole card. Raises NotFoundError when no character has that id."""
        found = read(self._engine, lambda connection: repository.get_card(connection, character_id))
        if found is None:
            raise NotFoundError(_NO_CHARACTER)
        return found

    def update(self, character_id: str, raw_card: Mapping[str, Any]) -> StoredCharacter:
        """Saves `raw_card` as the character's card, the user's own edit. It is authoritative: the locks it carries
        are the new locks, so a locked trait or field changes only when the same save unlocks it.

        Returns the character. A card equal to the stored one writes nothing and keeps the version number; any other
        appends a version with actor `user` and a summary of the sections that changed.

        Raises NotFoundError for an unknown character and ValidationFailedError for what validate_card refuses; the
        stored card stays as it was in both cases.
        """
        card = validate_card(raw_card)
        return self._save(character_id, lambda _current: card, actor="user")

    def apply_proposal(self, character_id: str, raw_card: Mapping[str, Any]) -> StoredCharacter:
        """Saves a card an automatic source proposes, such as a reviewed `identity_candidate`. Locked traits and locked
        visual fields of the stored card survive (`keep_locked`), and the version has actor `proposal`.

        Returns the character, unchanged when the proposal changes nothing once the locks are applied. Raises
        NotFoundError and ValidationFailedError as `update` does.
        """
        proposed = validate_card(raw_card)
        return self._save(character_id, lambda current: keep_locked(current, proposed), actor="proposal")

    def restore(self, character_id: str, version_no: int) -> StoredCharacter:
        """Appends a new version, actor `restore`, holding the card of version `version_no`, and makes it current.
        History is not rewritten. The version is appended even when the card equals the current one.

        Raises NotFoundError for an unknown character or version.
        """
        found = read(self._engine, lambda connection: repository.get_version(connection, character_id, version_no))
        if found is None:
            self.get(character_id)
            raise NotFoundError(_NO_VERSION)
        card, summary = found.card, f"Ripristinata la versione {version_no}"
        return self._save(character_id, lambda _current: card, actor="restore", force_version=True, note=summary)

    def list_versions(self, character_id: str) -> list[VersionSummary]:
        """Returns the versions of the character, newest first. Raises NotFoundError for an unknown character."""

        def work(connection: Connection) -> list[VersionSummary] | None:
            if repository.get(connection, character_id) is None:
                return None
            return repository.list_versions(connection, character_id)

        found = read(self._engine, work)
        if found is None:
            raise NotFoundError(_NO_CHARACTER)
        return found

    def get_version(self, character_id: str, version_no: int) -> StoredVersion:
        """Returns one version with its card. Raises NotFoundError for an unknown character or version."""
        found = read(self._engine, lambda connection: repository.get_version(connection, character_id, version_no))
        if found is None:
            self.get(character_id)
            raise NotFoundError(_NO_VERSION)
        return found

    def diff(self, character_id: str, from_no: int, to_no: int) -> list[FieldChange]:
        """Returns the fields whose value differs between versions `from_no` and `to_no`, in card order. Raises
        NotFoundError for an unknown character or version."""
        before = self.get_version(character_id, from_no)
        after = self.get_version(character_id, to_no)
        return diff_cards(before.card, after.card)

    def _save(
        self,
        character_id: str,
        build: Callable[[CharacterCard], CharacterCard],
        *,
        actor: str,
        force_version: bool = False,
        note: str | None = None,
    ) -> StoredCharacter:
        """Stores `build(current card)` in one transaction. It appends version `current + 1` when the card changes or
        `force_version` is set, and otherwise writes nothing."""
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> StoredCharacter:
            current = repository.get_card(connection, character_id)
            if current is None:
                raise NotFoundError(_NO_CHARACTER)
            card = build(current.card)
            changes = diff_cards(current.card, card)
            if not changes and not force_version:
                return current
            version_no = current.current_version_no + 1
            repository.replace_card(connection, character_id=character_id, card=card, version_no=version_no, now=now)
            repository.insert_version(
                connection,
                character_id=character_id,
                version_no=version_no,
                card=card,
                change_summary=note or _change_summary(changes),
                actor=actor,
                now=now,
            )
            saved = repository.get_card(connection, character_id)
            assert saved is not None
            return saved

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
