"""ScenarioService: creates, lists, edits, archives and deletes scenarios (docs/data-model.md, `scenarios`).

A scenario is only a template: a conversation copies it when it starts (Scenario.snapshot), so nothing done here
changes a running story.
"""

import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from lyrveil.consistency.content_guard import find_violation
from lyrveil.domain.errors import ContentPolicyBlockedError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.scenarios import (
    MAX_DESCRIPTION_CHARS,
    MAX_OPENING_CHARS,
    MAX_SETTING_CHARS,
    MAX_TITLE_CHARS,
    Scenario,
    ScenarioStatus,
)
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import scenarios as repository

_NO_SCENARIO = "Scenario non trovato."
_NO_CHARACTER = "Personaggio non trovato."
# The text fields, with the Italian name the refusals use and the longest value each takes.
_TEXT_FIELDS = (
    ("title", "titolo", MAX_TITLE_CHARS),
    ("description", "descrizione", MAX_DESCRIPTION_CHARS),
    ("setting", "ambientazione", MAX_SETTING_CHARS),
    ("opening_message", "messaggio iniziale", MAX_OPENING_CHARS),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _fold(value: str) -> str:
    """Returns `value` lowercase with accents removed, the form search compares."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _checked(fields: dict[str, Any]) -> dict[str, object]:
    """Returns `fields` with the text trimmed and a blank language as None.

    Raises ValidationFailedError for a blank title or a text over its limit, and ContentPolicyBlockedError, naming the
    field and never quoting it, when a text describes a minor: the text is read by the model in every conversation that
    starts from the scenario, so the safeguard of ADR 0018 applies as it does to a character card.
    """
    clean: dict[str, object] = {name: (fields.get(name) or "").strip() for name, _, _ in _TEXT_FIELDS}
    for name, label, limit in _TEXT_FIELDS:
        value = str(clean[name])
        if len(value) > limit:
            raise ValidationFailedError(f"Il campo «{label}» supera {limit} caratteri.")
        if find_violation(value) is not None:
            raise ContentPolicyBlockedError(
                f"Il campo «{label}» descrive un minore o un contenuto che l'app non consente. Modifica il testo."
            )
    if not clean["title"]:
        raise ValidationFailedError("Il titolo non può essere vuoto.")
    clean["character_id"] = fields.get("character_id") or None
    clean["language"] = (fields.get("language") or "").strip() or None
    return clean


class ScenarioService:
    """Reads and changes the scenarios of `engine`. `clock` returns the aware current time; tests pass a fake one."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def create(self, fields: dict[str, Any]) -> Scenario:
        """Returns a new active scenario. `fields` holds `title` (required) and optionally `description`, `setting`,
        `opening_message`, `character_id` (None means any character) and `language` (None inherits).

        Raises what `_checked` raises, and NotFoundError when `character_id` names no character.
        """
        clean = _checked(fields)
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Scenario:
            self._require_character(connection, clean)
            scenario_id = new_id()
            repository.insert(connection, scenario_id, clean, now)
            created = repository.get(connection, scenario_id)
            assert created is not None
            return created

        return write(self._engine, work)

    def list_scenarios(
        self,
        status: ScenarioStatus = ScenarioStatus.ACTIVE,
        character_id: str | None = None,
        q: str | None = None,
    ) -> list[Scenario]:
        """Returns the scenarios in `status`, newest change first. `character_id` keeps those usable with that
        character. A non-blank `q` keeps those whose title, description, setting or opening message contains every
        word of it, with case and accents ignored; it is plain text."""
        found = read(self._engine, lambda connection: repository.list_all(connection, status, character_id))
        words = _fold(q or "").split()
        if not words:
            return found

        def matches(scenario: Scenario) -> bool:
            parts = (scenario.title, scenario.description, scenario.setting, scenario.opening_message)
            haystack = _fold(" ".join(parts))
            return all(word in haystack for word in words)

        return [scenario for scenario in found if matches(scenario)]

    def get(self, scenario_id: str) -> Scenario:
        """Returns the scenario in any status; raises NotFoundError when none has that id."""
        found = read(self._engine, lambda connection: repository.get(connection, scenario_id))
        if found is None:
            raise NotFoundError(_NO_SCENARIO)
        return found

    def update(self, scenario_id: str, fields: dict[str, Any]) -> Scenario:
        """Replaces every editable field of the scenario (PUT) and returns it. Conversations already started from it
        keep their copy. Raises NotFoundError for an unknown scenario or character, and what `_checked` raises."""
        clean = _checked(fields)
        now = to_utc_text(self._clock())

        def work(connection: Any) -> Scenario:
            if repository.get(connection, scenario_id) is None:
                raise NotFoundError(_NO_SCENARIO)
            self._require_character(connection, clean)
            repository.update(connection, scenario_id, now, **clean)
            updated = repository.get(connection, scenario_id)
            assert updated is not None
            return updated

        return write(self._engine, work)

    def set_archived(self, scenario_id: str, archived: bool) -> Scenario:
        """Archives or restores the scenario and returns it. An archived scenario starts no new conversation. Raises
        NotFoundError for an unknown scenario."""
        now = to_utc_text(self._clock())
        status = ScenarioStatus.ARCHIVED if archived else ScenarioStatus.ACTIVE

        def work(connection: Any) -> Scenario:
            if repository.get(connection, scenario_id) is None:
                raise NotFoundError(_NO_SCENARIO)
            repository.update(connection, scenario_id, now, status=status.value)
            changed = repository.get(connection, scenario_id)
            assert changed is not None
            return changed

        return write(self._engine, work)

    def delete(self, scenario_id: str) -> None:
        """Removes the scenario for good. The conversations that started from it keep their copy and lose only the
        link. Raises NotFoundError for an unknown scenario."""

        def work(connection: Any) -> None:
            if repository.get(connection, scenario_id) is None:
                raise NotFoundError(_NO_SCENARIO)
            repository.delete(connection, scenario_id)

        write(self._engine, work)

    @staticmethod
    def _require_character(connection: Any, clean: dict[str, object]) -> None:
        character_id = clean["character_id"]
        if character_id is not None and not repository.character_exists(connection, str(character_id)):
            raise NotFoundError(_NO_CHARACTER)
