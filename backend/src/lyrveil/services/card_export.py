"""CardExportService: writes a character as a Character Card V3 or V2 JSON file, or as a PNG of its primary avatar that
carries both (ADR 0047, Export; docs/character-system.md, Character cards).

The free text is rendered from the current card by pure, deterministic functions, so two exports of an unchanged card
differ only in `modification_date`. `extensions.lyrveil` holds the complete native card and the hash of the rendered
text, which the import's round trip compares. An imported character keeps the fields of its stored source that the ADR
lists. A card never holds a conversation, a memory, a persona, another image or the user's name.
"""

import base64
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.context.blocks import TRAIT_LINES, canonical_identity
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.characters import StoredCharacter
from lyrveil.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from lyrveil.domain.exchange import FORMAT_NAME, FORMAT_VERSION
from lyrveil.domain.scenarios import Scenario
from lyrveil.domain.tavern_card import SPEC_V2, SPEC_V3, CardData, card_data
from lyrveil.jobs.scheduler import read
from lyrveil.media import images
from lyrveil.media.store import MediaPathError, MediaStore
from lyrveil.persistence import card_imports as import_repository
from lyrveil.persistence import characters as character_repository
from lyrveil.services.assets import CharacterAssetService
from lyrveil.services.character_exchange import file_stem
from lyrveil.services.scenarios import ScenarioService

FORMAT_V3_JSON = "v3json"
FORMAT_V2_JSON = "v2json"
FORMAT_PNG = "png"
FORMATS = (FORMAT_V3_JSON, FORMAT_V2_JSON, FORMAT_PNG)

_NO_CHARACTER = "Il personaggio non esiste."
_BAD_FORMAT = "Il formato non è valido: usa v3json, v2json o png."
_NO_AVATAR = "Il personaggio non ha un avatar principale: scegline uno, oppure esporta la card in JSON."
_MISSING_FILE = "L'avatar del personaggio non è disponibile."
_NO_SCENARIO = "Lo scenario non esiste."

_USER_LINE = "{{user}}: "
_CHAR_LINE = "{{char}}: "
# The Italian visual fields that describe the look, with their labels, in the order they are written.
_LOOK_LINES = (
    ("face", "Viso"),
    ("hair", "Capelli"),
    ("eyes", "Occhi"),
    ("body", "Corpo"),
    ("clothing", "Abbigliamento"),
    ("accessories", "Accessori"),
    ("distinctive_marks", "Segni particolari"),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CardTexts:
    """The free text that depends on the card alone: `description` holds the identity and the look, `personality` the
    traits and `mes_example` the examples as `<START>` blocks. The placeholders `{{char}}` and `{{user}}` stay as is."""

    description: str
    personality: str
    mes_example: str


@dataclass(frozen=True)
class ExportedCard:
    """A card file ready to send: its bytes, its media type and an ASCII file name."""

    content: bytes
    media_type: str
    file_name: str


@dataclass(frozen=True)
class KeptFields:
    """What a card file carries besides the rendered text: the kept fields of the stored source, or the defaults of a
    character without one."""

    creator: str
    creator_notes: str
    tags: list[str]
    source: list[str]
    character_version: str
    creation_date: int | float
    extensions: dict[str, Any]


def render_texts(card: CharacterCard) -> CardTexts:
    """Returns the rendered free text of `card`, always the same for the same card.

    `description` is block 2 without its traits (the identity lines of `lyrveil.context.blocks`) followed, when the card
    has any, by an `Aspetto:` list of its Italian visual fields; `personality` is the trait lines of block 2, one per
    category in template order; `mes_example` is one `<START>` block per example with a `{{user}}:` line when the
    example has a user message, and a `{{char}}:` line. Every field may be empty.
    """
    identity = canonical_identity(card.model_copy(update={"traits": []}))
    visual = card.visual
    look = [f"{label}: {getattr(visual, name)}" for name, label in _LOOK_LINES if getattr(visual, name)]
    description = "\n\n".join(part for part in (identity, "\n".join(["Aspetto:", *look]) if look else "") if part)
    traits = [
        f"{label}: {'; '.join(trait.description for trait in card.traits if trait.category == category)}"
        for category, label in TRAIT_LINES
        if any(trait.category == category for trait in card.traits)
    ]
    blocks = []
    for example in card.communication.examples:
        lines = ["<START>"]
        if example.user:
            lines.append(f"{_USER_LINE}{example.user}")
        lines.append(f"{_CHAR_LINE}{example.character}")
        blocks.append("\n".join(lines))
    return CardTexts(description=description, personality="\n".join(traits), mes_example="\n".join(blocks))


def text_sha256(texts: CardTexts) -> str:
    """Returns the hex SHA-256 of the three rendered texts, which `extensions.lyrveil.text_sha256` records.

    It covers what `render_texts` makes from the card, and not the scenario, `first_mes` and `alternate_greetings`:
    those come from the scenarios the user picks and cannot be rendered again from the embedded card. The hash is of
    the compact JSON of the texts with sorted keys and ASCII escapes, so it does not depend on an encoding.
    """
    payload = json.dumps(
        {"description": texts.description, "personality": texts.personality, "mes_example": texts.mes_example},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _unix_seconds(moment: str) -> int:
    """Returns the Unix time in whole seconds of a stored UTC timestamp such as 2026-11-07T10:00:00.000Z."""
    return int(datetime.fromisoformat(moment).timestamp())


def _full_name(card: CharacterCard) -> str:
    identity = card.identity
    return identity.first_name if not identity.last_name else f"{identity.first_name} {identity.last_name}"


def _scenario_text(scenario: Scenario) -> str:
    return "\n\n".join(part for part in (scenario.description, scenario.setting) if part)


def kept_fields(stored: StoredCharacter, source: dict[str, Any] | None) -> KeptFields:
    """Returns the kept fields: those of the source of an imported character, with every `extensions` key but
    `lyrveil`, else an empty creator, the version number as `character_version` and `created_at` as `creation_date`."""
    created = _unix_seconds(stored.created_at)
    if source is None:
        return KeptFields("", "", [], [], str(stored.current_version_no), created, {})
    data: CardData = card_data(source)
    return KeptFields(
        creator=data.creator,
        creator_notes=data.creator_notes,
        tags=list(data.tags),
        source=list(data.source),
        character_version=data.character_version,
        creation_date=created if data.creation_date is None else data.creation_date,
        extensions={key: value for key, value in data.extensions.items() if key != "lyrveil"},
    )


def build_v2(
    card: CharacterCard,
    texts: CardTexts,
    scenarios: Sequence[Scenario],
    kept: KeptFields,
) -> dict[str, Any]:
    """Returns the Character Card V2 envelope of `card`.

    The first of `scenarios` gives `scenario` and `first_mes`, each later one an alternate greeting when it has an
    opening message; a scenario's description and setting make its text. `system_prompt` and
    `post_history_instructions` are empty, and `character_book` is absent until the character has its own lore book.
    """
    first = scenarios[0] if scenarios else None
    extensions = {
        **kept.extensions,
        "lyrveil": {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "card": card.model_dump(mode="json"),
            "text_sha256": text_sha256(texts),
        },
    }
    return {
        "spec": SPEC_V2,
        "spec_version": "2.0",
        "data": {
            "name": _full_name(card),
            "description": texts.description,
            "personality": texts.personality,
            "scenario": "" if first is None else _scenario_text(first),
            "first_mes": "" if first is None else first.opening_message,
            "mes_example": texts.mes_example,
            "creator_notes": kept.creator_notes,
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": [item.opening_message for item in scenarios[1:] if item.opening_message],
            "tags": kept.tags,
            "creator": kept.creator,
            "character_version": kept.character_version,
            "extensions": extensions,
        },
    }


def build_v3(
    card: CharacterCard,
    texts: CardTexts,
    scenarios: Sequence[Scenario],
    kept: KeptFields,
    *,
    modified: int,
) -> dict[str, Any]:
    """Returns the Character Card V3 envelope: the fields of `build_v2` plus `source`, `group_only_greetings` (empty),
    `creation_date` and `modification_date`, the time of the export in Unix seconds."""
    document = build_v2(card, texts, scenarios, kept)
    data: dict[str, Any] = document["data"]
    data.update(
        source=kept.source,
        group_only_greetings=[],
        creation_date=kept.creation_date,
        modification_date=modified,
    )
    return {"spec": SPEC_V3, "spec_version": "3.0", "data": data}


def _json_bytes(document: dict[str, Any]) -> bytes:
    """Returns `document` as UTF-8 JSON. A text with a lone surrogate, which an imported card may hold, cannot be UTF-8,
    so it is written with ASCII escapes instead."""
    try:
        return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(document, ensure_ascii=True, indent=2).encode("ascii")


class CardExportService:
    """Exports the characters of `engine`, reading avatars from `media`. `clock` returns the aware current time and
    gives `modification_date`."""

    def __init__(self, engine: Engine, media: MediaStore, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._media = media
        self._clock = clock
        self._assets = CharacterAssetService(engine, media, clock)
        self._scenarios = ScenarioService(engine, clock)

    def export(self, character_id: str, card_format: str, scenario_ids: Sequence[str] = ()) -> ExportedCard:
        """Returns the card file of the character in `card_format` (one of FORMATS), made from its current card.

        `scenario_ids` are the scenarios to export, in order: the first becomes `scenario` and `first_mes`, the others
        alternate greetings; a repeated id counts once. A scenario must be usable with this character. For `png` the
        file is the primary avatar re-encoded as PNG with a `ccv3` chunk (V3) and a `chara` chunk (V2), each the base64
        of the UTF-8 JSON; the JSON formats are the card itself.

        Raises ValidationFailedError for another format, NotFoundError for an unknown character, a scenario that does
        not exist or belongs to another character, or an avatar file that is missing, and ConflictError for `png` when
        the character has no primary avatar (the JSON formats still work).
        """
        if card_format not in FORMATS:
            raise ValidationFailedError(_BAD_FORMAT)
        found = read(self._engine, lambda connection: self._load(connection, character_id))
        if found is None:
            raise NotFoundError(_NO_CHARACTER)
        stored, source = found
        scenarios = self._pick_scenarios(character_id, scenario_ids)
        avatar = self._primary_avatar(character_id) if card_format == FORMAT_PNG else None
        card = stored.card
        texts = render_texts(card)
        kept = kept_fields(stored, source)
        stem = file_stem(card.identity.first_name)
        v2 = build_v2(card, texts, scenarios, kept)
        if card_format == FORMAT_V2_JSON:
            return ExportedCard(_json_bytes(v2), "application/json", f"{stem}.v2.json")
        v3 = build_v3(card, texts, scenarios, kept, modified=int(self._clock().timestamp()))
        if card_format == FORMAT_V3_JSON:
            return ExportedCard(_json_bytes(v3), "application/json", f"{stem}.json")
        assert avatar is not None
        chunks = {"chara": _chunk(v2), "ccv3": _chunk(v3)}
        return ExportedCard(images.to_png_with_text(avatar, chunks), "image/png", f"{stem}.png")

    @staticmethod
    def _load(connection: Connection, character_id: str) -> tuple[StoredCharacter, dict[str, Any] | None] | None:
        """Returns the character and the parsed stored source card of an imported one (None for another), or None for an
        unknown character."""
        stored = character_repository.get_card(connection, character_id)
        if stored is None:
            return None
        row = import_repository.source_of_character(connection, character_id)
        return stored, None if row is None else json.loads(row.source_json)

    def _pick_scenarios(self, character_id: str, scenario_ids: Sequence[str]) -> list[Scenario]:
        picked: list[Scenario] = []
        for scenario_id in dict.fromkeys(scenario_ids):
            scenario = self._scenarios.get(scenario_id)
            if scenario.character_id not in (None, character_id):
                raise NotFoundError(_NO_SCENARIO)
            picked.append(scenario)
        return picked

    def _primary_avatar(self, character_id: str) -> bytes:
        """Returns the bytes of the primary avatar. Raises ConflictError without one and NotFoundError when its file is
        missing."""
        primary = next((asset for asset in self._assets.list_assets(character_id, "avatar") if asset.is_primary), None)
        if primary is None:
            raise ConflictError(_NO_AVATAR)
        try:
            return self._media.read(primary.file_path)
        except (OSError, MediaPathError):
            raise NotFoundError(_MISSING_FILE) from None


def _chunk(document: dict[str, Any]) -> str:
    """Returns the value of a card `tEXt` chunk: the base64 of the UTF-8 JSON of `document`, written compactly."""
    try:
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        raw = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return base64.b64encode(raw).decode("ascii")
