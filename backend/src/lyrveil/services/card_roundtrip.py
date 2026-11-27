"""Reads the Lyrveil card embedded in an imported card's `extensions.lyrveil` and compares the file's free text with it
(ADR 0047, Round trip).

The embedded card is data from a file, so it never skips a rule: it goes through `validate_card`, the same card rules
and content guard as a native import, whatever the hash says. The hash only decides which source is preselected.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.errors import ContentPolicyBlockedError, ImportInvalidError, ValidationFailedError
from lyrveil.domain.exchange import FORMAT_NAME, FORMAT_VERSION
from lyrveil.domain.tavern_card import CardData
from lyrveil.services.card_export import CardTexts, render_texts, text_sha256
from lyrveil.services.card_rules import validate_card

# The three texts the hash covers, in the order they are reported.
TEXT_FIELDS = ("description", "personality", "mes_example")


@dataclass(frozen=True)
class EmbeddedLyrveil:
    """The validated native card of a card file. `text_matches` says that the file's `description`, `personality` and
    `mes_example` hash to the recorded `text_sha256`, so nothing was edited elsewhere. `changed_fields` names the
    texts of the file that differ from the ones the embedded card renders; it is empty when `text_matches` is true,
    and may be empty without it when only the embedded card was changed."""

    card: CharacterCard
    text_matches: bool
    changed_fields: list[str]


def read_embedded(data: CardData, *, refuse: bool) -> EmbeddedLyrveil | None:
    """Returns the embedded Lyrveil card of `data`, or None when there is none or it cannot be used.

    None covers a missing or malformed `extensions.lyrveil`, another `format`, a `format_version` that is not an
    integer from 1 to the one this build reads, and an embedded card the rules refuse when the texts were edited. When
    the hash matches, the embedded card is what the import uses, so a card the rules refuse is not skipped: with
    `refuse` it raises ImportInvalidError (the card rules) or ContentPolicyBlockedError (the content guard), and
    without it the result is None. Nothing is written.
    """
    block: Any = data.extensions.get("lyrveil")
    if not isinstance(block, Mapping) or block.get("format") != FORMAT_NAME:
        return None
    version = block.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= FORMAT_VERSION:
        return None
    recorded, raw = block.get("text_sha256"), block.get("card")
    if not isinstance(recorded, str) or not isinstance(raw, Mapping):
        return None
    file_texts = CardTexts(data.description, data.personality, data.mes_example)
    matches = text_sha256(file_texts) == recorded
    try:
        card = validate_card(raw)
    except (ValidationFailedError, ContentPolicyBlockedError) as error:
        if not matches or not refuse:
            return None
        if isinstance(error, ValidationFailedError):
            raise ImportInvalidError(error.detail) from None
        raise
    rendered = render_texts(card)
    changed = [] if matches else [name for name in TEXT_FIELDS if getattr(file_texts, name) != getattr(rendered, name)]
    return EmbeddedLyrveil(card=card, text_matches=matches, changed_fields=changed)
