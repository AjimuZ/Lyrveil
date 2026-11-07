"""Scenario values and rules that hold without a database (docs/data-model.md, `scenarios`)."""

from dataclasses import dataclass
from enum import StrEnum

MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 2000
MAX_SETTING_CHARS = 2000
MAX_OPENING_CHARS = 2000


class ScenarioStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Scenario:
    """One scenario row. `character_id` None means usable with any character; `language` None inherits the character's.

    `description`, `setting` and `opening_message` are never None, only empty.
    """

    id: str
    title: str
    description: str
    setting: str
    opening_message: str
    character_id: str | None
    language: str | None
    status: ScenarioStatus
    created_at: str
    updated_at: str

    def snapshot(self) -> dict[str, object]:
        """Returns the copy a conversation stores when it starts from this scenario (`scenario_snapshot_json`).

        Holds only what the story reads, so later edits, archiving or deleting of the scenario never change a running
        conversation.
        """
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "setting": self.setting,
            "opening_message": self.opening_message,
            "language": self.language,
        }
