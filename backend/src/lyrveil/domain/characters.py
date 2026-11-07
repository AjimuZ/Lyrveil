"""Character card values that hold without a database (docs/data-model.md, characters)."""

from dataclasses import dataclass

from lyrveil.domain.card import CharacterCard


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
class StoredCharacter:
    """A character with its whole card, as `GET /characters/{id}` returns it. `status` is `active` or `archived`."""

    id: str
    status: str
    is_demo: bool
    current_version_no: int
    created_at: str
    updated_at: str
    card: CharacterCard


@dataclass(frozen=True)
class VersionSummary:
    """One row of a character's version list. `actor` is `user`, `import`, `restore`, `duplicate` or `proposal`."""

    version_no: int
    change_summary: str
    actor: str
    created_at: str


@dataclass(frozen=True)
class StoredVersion:
    """A version with the whole card it holds."""

    summary: VersionSummary
    card: CharacterCard


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
