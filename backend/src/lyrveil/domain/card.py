"""The character card of docs/character-system.md (Card schema): its structure and bounds, with no database.

Vocabulary membership and tag sanitizing need the tag file, so lyrveil.services.card_rules applies them after this
model has checked the shape. A refusal raised here carries an Italian message, which the service shows as the detail.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

MIN_AGE = 18  # ADR 0018: every character is an adult.
MAX_AGE = 120
MAX_NAME_LENGTH = 80

GrammaticalGender = Literal["femminile", "maschile", "non_indicato"]
LanguageCode = Literal["it", "en", "fr", "de", "es"]
TraitCategory = Literal[
    "main_trait",
    "value",
    "motivation",
    "preference",
    "flaw",
    "fear",
    "habit",
    "humor",
    "temperament",
    "relational_style",
]
Formality = Literal["molto_informale", "informale", "neutra", "formale"]
MessageLength = Literal["brevissimi", "brevi", "medi", "lunghi"]
Frequency = Literal["raramente", "a_volte", "spesso"]
Expressiveness = Literal["contenuta", "media", "vivace"]
AddressForm = Literal["tu", "lei"]
Narration = Literal["nessuna", "leggera", "libera"]
EmojiUse = Literal["mai", "raramente", "a_volte", "spesso"]
SubjectTag = Literal["1girl", "1boy", "1other"]
SeedPolicy = Literal["random", "fixed", "base_offset"]
# The visual fields a user can lock; ADR 0035 maps each to the slots it protects.
LockableField = Literal["hair", "eyes", "face", "body", "distinctive_marks", "accessories", "art_style", "clothing"]

# Keys the image form reserves for the usual outfit and the outfit a scene chooses.
RESERVED_OUTFIT_KEYS = frozenset({"usual", "scene"})
MAX_OUTFITS = 5
MAX_SEED = 2**63 - 1  # The largest value a SQLite INTEGER holds.

_OUTFIT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")

Text = Annotated[str, StringConstraints(strip_whitespace=True)]
AvoidedPhrase = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]


def _without_blanks(values: list[str]) -> list[str]:
    return [value for value in values if value]


class _Strict(BaseModel):
    """Unknown fields are refused, so a typo is not silently lost (docs/character-system.md, Import rules)."""

    model_config = ConfigDict(extra="forbid")


class Identity(_Strict):
    first_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_LENGTH)]
    last_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_NAME_LENGTH)] | None = None
    age: Annotated[int, Field(ge=MIN_AGE, le=MAX_AGE)]
    grammatical_gender: GrammaticalGender = "non_indicato"
    narrative_identity: Text = ""
    biography: Text = ""
    occupation: Text = ""
    interests: list[Text] = []
    background: Text = ""
    setting: Text = ""
    primary_language: LanguageCode = "it"

    @field_validator("last_name")
    @classmethod
    def _blank_last_name_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("interests")
    @classmethod
    def _drop_blank_interests(cls, value: list[str]) -> list[str]:
        return _without_blanks(value)


class Trait(_Strict):
    category: TraitCategory
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    locked: bool = False


class ExampleExchange(_Strict):
    """A line of the character, with the user message it answers when there is one."""

    user: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    character: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

    @field_validator("user")
    @classmethod
    def _blank_user_is_none(cls, value: str | None) -> str | None:
        return value or None


class CommunicationProfile(_Strict):
    formality: Formality = "informale"
    message_length: MessageLength = "brevi"
    question_frequency: Frequency = "a_volte"
    punctuation: Text = ""
    colloquial_style: Text = ""
    expressiveness: Expressiveness = "media"
    initiative_frequency: Frequency = "a_volte"
    address_form: AddressForm = "tu"
    narration: Narration = "nessuna"
    emoji: EmojiUse = "raramente"
    examples: Annotated[list[ExampleExchange], Field(max_length=8)] = []
    recurring_expressions: Annotated[list[Text], Field(max_length=12)] = []
    avoid: Annotated[list[AvoidedPhrase], Field(max_length=8)] = []

    @field_validator("recurring_expressions", "avoid")
    @classmethod
    def _drop_blank_phrases(cls, value: list[str]) -> list[str]:
        return _without_blanks(value)


class VisualIdentityEn(_Strict):
    """The English identity slots. Every value is a tag of the vocabulary except `extra`, which holds free tags; the
    service checks both, since only it can read the tag file."""

    hair_color: str | None = None
    hair_length: str | None = None
    eye_color: str | None = None
    skin: str | None = None
    hairstyle: Annotated[list[str], Field(max_length=4)] = []
    face: Annotated[list[str], Field(max_length=4)] = []
    body: Annotated[list[str], Field(max_length=4)] = []
    marks: Annotated[list[str], Field(max_length=4)] = []
    accessories: Annotated[list[str], Field(max_length=4)] = []
    style: Annotated[list[str], Field(max_length=4)] = []
    extra: Annotated[list[str], Field(max_length=6)] = []


class Outfit(_Strict):
    key: str
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    tags_en: Annotated[list[str], Field(max_length=12)] = []

    @field_validator("key")
    @classmethod
    def _key_is_free(cls, value: str) -> str:
        if value in RESERVED_OUTFIT_KEYS:
            raise ValueError("La chiave di un abito non può essere «usual» né «scene».")
        if not _OUTFIT_KEY.fullmatch(value):
            raise ValueError(
                "La chiave di un abito usa minuscole, cifre e «_», inizia con una lettera e ha al massimo 24 caratteri."
            )
        return value


class VisualProfile(_Strict):
    face: Text = ""
    hair: Text = ""
    eyes: Text = ""
    body: Text = ""
    clothing: Text = ""
    accessories: Text = ""
    distinctive_marks: Text = ""
    art_style: Text = ""
    subject_tag: SubjectTag = "1other"
    identity_en: VisualIdentityEn = Field(default_factory=VisualIdentityEn)
    usual_outfit_en: Annotated[list[str], Field(max_length=12)] = []
    outfits: Annotated[list[Outfit], Field(max_length=MAX_OUTFITS)] = []
    persistent_negative_en: Text = ""
    locked_fields: list[LockableField] = []
    seed_policy: SeedPolicy = "random"
    base_seed: Annotated[int, Field(ge=0, le=MAX_SEED)] | None = None

    @field_validator("outfits")
    @classmethod
    def _keys_are_unique(cls, value: list[Outfit]) -> list[Outfit]:
        keys = [outfit.key for outfit in value]
        if len(set(keys)) != len(keys):
            raise ValueError("Due abiti non possono avere la stessa chiave.")
        return value

    @field_validator("locked_fields")
    @classmethod
    def _locks_are_unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _seed_policy_has_its_seed(self) -> Self:
        if self.seed_policy != "random" and self.base_seed is None:
            raise ValueError("Con questa politica del seed serve un valore iniziale.")
        return self


class CharacterCard(_Strict):
    identity: Identity
    traits: list[Trait] = []
    communication: CommunicationProfile = Field(default_factory=CommunicationProfile)
    visual: VisualProfile = Field(default_factory=VisualProfile)
