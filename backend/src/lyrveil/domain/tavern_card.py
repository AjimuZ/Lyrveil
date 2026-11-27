"""Character Card V1, V2 and V3 models and parser (ADR 0047; docs/character-system.md, Character cards).

`parse_card` reads a card from the bytes of an uploaded file: JSON, or the `ccv3` or `chara` `tEXt` chunk of a PNG or
APNG. The format comes from the content, never from a file name. PNG chunks are read here, with length and CRC checks,
rather than by Pillow, so no global Pillow limit changes; decoding the image itself is the avatar step's job, with the
upload limits. Every string of the card passes the content guard of ADR 0037, except data URIs. The parser holds no
state, reads no file and no setting, and never fetches a remote `uri`.
"""

import base64
import binascii
import hashlib
import json
import re
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from lyrveil.consistency.content_guard import check_texts
from lyrveil.domain.errors import DomainError, ImportInvalidError, PayloadTooLargeError, UnsupportedMediaError
from lyrveil.domain.exchange import MAX_IMPORT_BYTES

MAX_CARD_BYTES = MAX_IMPORT_BYTES
MAX_JSON_DEPTH = 64

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# A local file header or the end record of an empty archive: CHARX is a zip archive, which P10 does not read.
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")
# The PNG specification bounds a chunk length to 2^31 - 1.
_MAX_CHUNK_LENGTH = 2**31 - 1

SPEC_V2 = "chara_card_v2"
SPEC_V3 = "chara_card_v3"
# The six texts of a V1 card, at the top level with no envelope.
V1_FIELDS = ("name", "description", "personality", "scenario", "first_mes", "mes_example")

# A base64 data URI and nothing else; such a string is an embedded file, not text, so the content guard skips it. A
# string that merely starts with "data:" is still checked.
_DATA_URI = re.compile(r"data:[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+(?:;[A-Za-z0-9.+=-]+)*;base64,[A-Za-z0-9+/]*={0,2}")


class CardSpec(StrEnum):
    """The specification a card follows, read from its content."""

    V1 = "v1"
    V2 = "v2"
    V3 = "v3"


class CardContainer(StrEnum):
    """What the file was: plain JSON, a PNG, or an APNG (a PNG with an `acTL` chunk)."""

    JSON = "json"
    PNG = "png"
    APNG = "apng"


class CardChunk(StrEnum):
    """The `tEXt` keyword a PNG card was read from. `ccv3` wins when both are present."""

    CCV3 = "ccv3"
    CHARA = "chara"


_SPECS = {SPEC_V2: CardSpec.V2, SPEC_V3: CardSpec.V3}


class CardFailure(StrEnum):
    """Why a file was refused. Each reason has its own Italian detail, and the error class gives the HTTP answer:
    413 for TOO_LARGE, 415 for NOT_A_CARD_FILE and CHARX, 422 IMPORT_INVALID for the others."""

    TOO_LARGE = "too_large"
    NOT_A_CARD_FILE = "not_a_card_file"  # Neither JSON nor PNG.
    CHARX = "charx"
    BAD_PNG = "bad_png"  # A PNG signature, but no IHDR first, a chunk type that is not four letters, or no IEND.
    CHUNK_LENGTH = "chunk_length"  # A chunk runs past the end of the file or over the PNG bound.
    CHUNK_CRC = "chunk_crc"
    NO_CARD_CHUNK = "no_card_chunk"  # A valid PNG with neither a `ccv3` nor a `chara` chunk.
    BAD_BASE64 = "bad_base64"
    BAD_JSON = "bad_json"  # Not UTF-8, not JSON, or NaN and Infinity, which JSON does not have.
    TOO_DEEP = "too_deep"
    MISSING_SPEC = "missing_spec"  # Not an object, or no `spec` and not a V1 card.
    UNKNOWN_SPEC = "unknown_spec"
    BAD_FIELDS = "bad_fields"  # A known field of the wrong type, or a V2 or V3 card without a `data` object.


_DETAILS = {
    CardFailure.TOO_LARGE: "Il file supera il limite di 64 MB.",
    CardFailure.NOT_A_CARD_FILE: "Il file non è una card JSON né un'immagine PNG.",
    CardFailure.CHARX: "Le card CHARX non sono supportate. Usa una card JSON o PNG.",
    CardFailure.BAD_PNG: "L'immagine PNG è danneggiata.",
    CardFailure.CHUNK_LENGTH: "L'immagine PNG è danneggiata: un blocco ha una lunghezza non valida.",
    CardFailure.CHUNK_CRC: "L'immagine PNG è danneggiata: il controllo CRC di un blocco non torna.",
    CardFailure.NO_CARD_CHUNK: "L'immagine PNG non contiene una card.",
    CardFailure.BAD_BASE64: "La card nell'immagine non è base64 valido.",
    CardFailure.BAD_JSON: "La card non è JSON valido.",
    CardFailure.TOO_DEEP: f"La card ha più di {MAX_JSON_DEPTH} livelli annidati.",
    CardFailure.MISSING_SPEC: "Il file non è una card: manca il campo spec.",
    CardFailure.UNKNOWN_SPEC: "La versione della card non è supportata: sono lette le card V1, V2 e V3.",
    CardFailure.BAD_FIELDS: "Il campo «{path}» della card non è valido.",
}


class CardRefusedError(DomainError):
    """A card file the parser refuses; `reason` tells which check failed, and the concrete class gives `code` and
    `status`. `detail` never quotes the card's text."""

    def __init__(self, reason: CardFailure, path: str = "") -> None:
        super().__init__(_DETAILS[reason].format(path=path))
        self.reason = reason


class CardTooLargeError(CardRefusedError, PayloadTooLargeError):
    """The file is over MAX_CARD_BYTES (413 `PAYLOAD_TOO_LARGE`)."""


class CardMediaError(CardRefusedError, UnsupportedMediaError):
    """The file is neither JSON nor PNG, a CHARX archive included (415 `UNSUPPORTED_MEDIA`)."""


class CardInvalidError(CardRefusedError, ImportInvalidError):
    """The file is JSON or PNG but not a readable card (422 `IMPORT_INVALID`)."""


def _refuse(reason: CardFailure, path: str = "") -> CardRefusedError:
    if reason is CardFailure.TOO_LARGE:
        return CardTooLargeError(reason, path)
    if reason in (CardFailure.NOT_A_CARD_FILE, CardFailure.CHARX):
        return CardMediaError(reason, path)
    return CardInvalidError(reason, path)


def _empty_text(value: object) -> object:
    return "" if value is None else value


def _empty_list(value: object) -> object:
    return [] if value is None else value


def _empty_object(value: object) -> object:
    return {} if value is None else value


# Writers often leave a field null; it reads as empty. Any other wrong type is refused as BAD_FIELDS.
Text = Annotated[str, BeforeValidator(_empty_text)]
TextList = Annotated[list[str], BeforeValidator(_empty_list)]
Extensions = Annotated[dict[str, Any], BeforeValidator(_empty_object)]


class _Open(BaseModel):
    """Unknown fields are kept: the specifications forbid destroying them, and the source is stored as read."""

    model_config = ConfigDict(extra="allow", frozen=True)


class BookEntry(_Open):
    """A `character_book` entry, with the fields the import shows; the rest stays in `model_extra`."""

    keys: TextList = []
    secondary_keys: TextList = []
    content: Text = ""
    name: Text = ""
    comment: Text = ""
    enabled: bool = True
    extensions: Extensions = {}


class CharacterBook(_Open):
    """The card's lorebook. It is kept in the source and becomes the character's own lore book in P12."""

    name: Text = ""
    description: Text = ""
    entries: Annotated[list[BookEntry], BeforeValidator(_empty_list)] = []
    extensions: Extensions = {}


class CardAsset(_Open):
    """A V3 asset. `uri` is a data URI, `ccdefault:` or a remote address, which is never fetched."""

    type: Text = ""
    uri: Text = ""
    name: Text = ""
    ext: Text = ""


class CardData(_Open):
    """The fields of a card in one model: the V1 texts, the V2 `data` fields and the V3 additions. A V1 card fills
    only its six texts. `name` is required; every other field defaults to empty."""

    name: str
    description: Text = ""
    personality: Text = ""
    scenario: Text = ""
    first_mes: Text = ""
    mes_example: Text = ""
    creator_notes: Text = ""
    system_prompt: Text = ""
    post_history_instructions: Text = ""
    alternate_greetings: TextList = []
    character_book: CharacterBook | None = None
    tags: TextList = []
    creator: Text = ""
    character_version: Text = ""
    extensions: Extensions = {}
    nickname: Text = ""
    creator_notes_multilingual: Annotated[dict[str, str], BeforeValidator(_empty_object)] = {}
    source: TextList = []
    group_only_greetings: TextList = []
    assets: Annotated[list[CardAsset], BeforeValidator(_empty_list)] = []
    creation_date: int | float | None = None
    modification_date: int | float | None = None


@dataclass(frozen=True)
class ParsedCard:
    """A card that passed every check. `source` is the JSON exactly as read, envelope included, which the import
    stores unchanged; `data` is its typed view. `chunk` is None for a JSON file, and `spec_version` for a V1 card or an
    envelope without a text `spec_version`. `sha256` is the hex hash of the whole file."""

    spec: CardSpec
    spec_version: str | None
    container: CardContainer
    chunk: CardChunk | None
    source: dict[str, Any]
    data: CardData
    sha256: str


def card_data(source: dict[str, Any]) -> CardData:
    """Returns the typed fields of a stored card `source`, which already passed the parser: the `data` of a V2 or V3
    envelope, or the card itself for V1."""
    return CardData.model_validate(source if "spec" not in source else source["data"])


def parse_card(content: bytes) -> ParsedCard:
    """Returns the card in `content`, the bytes of an uploaded file.

    A file over MAX_CARD_BYTES, a zip archive (CHARX) and a file that is neither JSON nor PNG are refused first. A PNG
    is read chunk by chunk up to IEND, every chunk's length and CRC checked; the first `ccv3` `tEXt` chunk is used,
    else the first `chara` one, and a broken `ccv3` is refused rather than skipped. Its text is base64 of UTF-8 JSON.
    The JSON must be an object at most MAX_JSON_DEPTH levels deep. `spec` `chara_card_v2` or `chara_card_v3` gives
    V2 or V3, with the fields in `data`; an object without `spec` that has a text `name` and another V1 text is V1.

    Raises CardRefusedError (one of its three subclasses) with the CardFailure reason, or ContentPolicyBlockedError
    naming the source path, such as `data.character_book.entries[3].content`, when a string describes a minor.
    """
    if len(content) > MAX_CARD_BYTES:
        raise _refuse(CardFailure.TOO_LARGE)
    chunk: CardChunk | None = None
    if content.startswith(PNG_SIGNATURE):
        container, chunk, payload = _read_png(content)
        source = _load_json(_decode_base64(payload))
    elif content.startswith(ZIP_SIGNATURES):
        raise _refuse(CardFailure.CHARX)
    else:
        container = CardContainer.JSON
        source = _load_json(_json_file_text(content))
    spec, spec_version, data = _read_card(source)
    check_texts(_card_texts(source, ""))
    return ParsedCard(
        spec=spec,
        spec_version=spec_version,
        container=container,
        chunk=chunk,
        source=source,
        data=data,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _read_png(content: bytes) -> tuple[CardContainer, CardChunk, bytes]:
    """Returns the container, the chosen keyword and the raw text of the card chunk. Chunks after IEND are ignored."""
    view = memoryview(content)
    offset = len(PNG_SIGNATURE)
    animated = False
    texts: dict[CardChunk, bytes] = {}
    first = True
    while True:
        if offset + 12 > len(content):
            raise _refuse(CardFailure.CHUNK_LENGTH)
        length = int.from_bytes(view[offset : offset + 4], "big")
        kind = bytes(view[offset + 4 : offset + 8])
        end = offset + 12 + length
        if length > _MAX_CHUNK_LENGTH or end > len(content):
            raise _refuse(CardFailure.CHUNK_LENGTH)
        if not (kind.isascii() and kind.isalpha()) or (first and kind != b"IHDR"):
            raise _refuse(CardFailure.BAD_PNG)
        body = view[offset + 8 : end - 4]
        if zlib.crc32(body, zlib.crc32(kind)) != int.from_bytes(view[end - 4 : end], "big"):
            raise _refuse(CardFailure.CHUNK_CRC)
        first = False
        if kind == b"acTL":
            animated = True
        elif kind == b"tEXt":
            keyword, separator, text = bytes(body).partition(b"\0")
            if separator and keyword in (b"ccv3", b"chara"):
                texts.setdefault(CardChunk(keyword.decode("ascii")), text)
        elif kind == b"IEND":
            break
        offset = end
    container = CardContainer.APNG if animated else CardContainer.PNG
    for chosen in (CardChunk.CCV3, CardChunk.CHARA):
        if chosen in texts:
            return container, chosen, texts[chosen]
    raise _refuse(CardFailure.NO_CARD_CHUNK)


def _decode_base64(payload: bytes) -> str:
    """Returns the UTF-8 text inside a chunk. ASCII whitespace in the base64 is ignored, any other stray byte is not."""
    try:
        decoded = base64.b64decode(b"".join(payload.split()), validate=True)
    except binascii.Error as error:
        raise _refuse(CardFailure.BAD_BASE64) from error
    try:
        return decoded.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _refuse(CardFailure.BAD_JSON) from error


def _json_file_text(content: bytes) -> str:
    """Returns the text of a file that is JSON by its content: UTF-8 whose first non-blank character opens an object
    or an array. Anything else is not a card file (415); a broken JSON text is BAD_JSON later (422)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _refuse(CardFailure.NOT_A_CARD_FILE) from error
    if not text.lstrip().startswith(("{", "[")):
        raise _refuse(CardFailure.NOT_A_CARD_FILE)
    return text


def _refuse_constant(name: str) -> object:
    raise ValueError(name)


def _load_json(text: str) -> dict[str, Any]:
    """Returns the top-level object of `text`. A depth over MAX_JSON_DEPTH is refused whether the decoder reaches its
    own recursion limit (a RecursionError) or not; a top-level value that is not an object has no `spec`."""
    try:
        value = json.loads(text, parse_constant=_refuse_constant)
    except RecursionError as error:
        raise _refuse(CardFailure.TOO_DEEP) from error
    except ValueError as error:
        raise _refuse(CardFailure.BAD_JSON) from error
    if _depth_over(value, MAX_JSON_DEPTH):
        raise _refuse(CardFailure.TOO_DEEP)
    if not isinstance(value, dict):
        raise _refuse(CardFailure.MISSING_SPEC)
    return value


def _depth_over(value: object, limit: int) -> bool:
    """True when `value` nests objects and arrays more than `limit` levels deep; the top-level container is level 1.
    Iterative, so a deep value cannot exhaust the stack."""
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict):
            children: list[Any] = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if level > limit:
            return True
        stack.extend((child, level + 1) for child in children)
    return False


def _read_card(source: dict[str, Any]) -> tuple[CardSpec, str | None, CardData]:
    """Returns the specification, the envelope's `spec_version` and the typed fields of `source`."""
    if "spec" not in source:
        if isinstance(source.get("name"), str) and any(field in source for field in V1_FIELDS[1:]):
            texts = {field: source[field] for field in V1_FIELDS if field in source}
            return CardSpec.V1, None, _validate(texts, "")
        raise _refuse(CardFailure.MISSING_SPEC)
    spec = _SPECS.get(source["spec"]) if isinstance(source["spec"], str) else None
    if spec is None:
        raise _refuse(CardFailure.UNKNOWN_SPEC)
    data = source.get("data")
    if not isinstance(data, dict):
        raise _refuse(CardFailure.BAD_FIELDS, "data")
    version = source.get("spec_version")
    return spec, version if isinstance(version, str) else None, _validate(data, "data")


def _validate(fields: dict[str, Any], prefix: str) -> CardData:
    try:
        return CardData.model_validate(fields)
    except ValidationError as error:
        location = error.errors()[0]["loc"]
        raise _refuse(CardFailure.BAD_FIELDS, _join_path(prefix, location)) from error


def _join_path(prefix: str, location: tuple[int | str, ...]) -> str:
    path = prefix
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path = f"{path}.{part}" if path else part
    return path


def _card_texts(value: object, path: str) -> Iterator[tuple[str, str]]:
    """Yields the source path and the value of every string below `value`, in file order, except base64 data URIs.
    Object members read `a.b`, array items `a[3]`. Keys are not yielded: no key reaches a prompt or the preview."""
    if isinstance(value, str):
        if not _DATA_URI.fullmatch(value):
            yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _card_texts(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _card_texts(item, f"{path}[{index}]")
