"""Character card values that hold without a database (docs/data-model.md, characters)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CharacterProfile:
    """The card fields the P02 reply prompt reads: identity, gender and the free-text parts of block 2.

    `grammatical_gender` is `femminile`, `maschile` or `non_indicato`. Empty text fields are left out of the prompt.
    """

    id: str
    first_name: str
    last_name: str | None
    age: int
    grammatical_gender: str
    narrative_identity: str
    occupation: str
    setting: str
    biography: str
    primary_language: str

    @property
    def full_name(self) -> str:
        return self.first_name if not self.last_name else f"{self.first_name} {self.last_name}"


@dataclass(frozen=True)
class CharacterSummary:
    """A character as the list and the new-chat picker show it. `status` is `active` or `archived`."""

    id: str
    first_name: str
    last_name: str | None
    age: int
    grammatical_gender: str
    primary_language: str
    status: str
    created_at: str
    updated_at: str

    @property
    def full_name(self) -> str:
        return self.first_name if not self.last_name else f"{self.first_name} {self.last_name}"
