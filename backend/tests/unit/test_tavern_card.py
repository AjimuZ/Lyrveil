"""Tests of lyrveil.domain.tavern_card: T-CRD-01 (parsing and refusals) and the unit part of T-CRD-04 (the content
guard on every string of a card, with its source path).

The JSON cards in tests/fixtures/tavern_cards are synthetic, written for Lyrveil; no third-party card is committed. The
PNG and APNG cards are assembled here from those JSON files, chunk by chunk, so every byte of them is visible in this
file; Pillow opens them, which shows the builder writes real images.
"""

import ast
import base64
import io
import json
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, PngImagePlugin

from lyrveil.domain import tavern_card
from lyrveil.domain.errors import ContentPolicyBlockedError
from lyrveil.domain.tavern_card import (
    MAX_CARD_BYTES,
    PNG_SIGNATURE,
    CardChunk,
    CardContainer,
    CardFailure,
    CardInvalidError,
    CardMediaError,
    CardRefusedError,
    CardSpec,
    CardTooLargeError,
    parse_card,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tavern_cards"


def _card(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


def _json(card: object) -> bytes:
    return json.dumps(card, ensure_ascii=False).encode("utf-8")


def _chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(body, zlib.crc32(kind))
    return len(body).to_bytes(4, "big") + kind + body + crc.to_bytes(4, "big")


def _text(keyword: str, card: object) -> bytes:
    """A `tEXt` chunk holding the base64 of the card's UTF-8 JSON, as the specifications embed it."""
    return _chunk(b"tEXt", keyword.encode("latin-1") + b"\0" + base64.b64encode(_json(card)))


_IHDR = _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
_PIXELS = zlib.compress(b"".join(b"\x00" + bytes([200, 80, 40]) * 2 for _ in range(2)))
_IEND = _chunk(b"IEND", b"")
# Offset of the first chunk after IHDR: the signature, then IHDR's length, type, 13 bytes and CRC.
_AFTER_IHDR = len(PNG_SIGNATURE) + len(_IHDR)


def _png(*chunks: bytes) -> bytes:
    """A 2 x 2 RGB PNG with `chunks` between IHDR and IDAT."""
    return PNG_SIGNATURE + _IHDR + b"".join(chunks) + _chunk(b"IDAT", _PIXELS) + _IEND


def _apng(*chunks: bytes) -> bytes:
    """A two-frame APNG: acTL and `chunks`, then a frame in IDAT and a second one in fdAT."""

    def frame_control(sequence: int) -> bytes:
        return _chunk(b"fcTL", struct.pack(">IIIIIHHBB", sequence, 2, 2, 0, 0, 1, 10, 0, 0))

    return (
        PNG_SIGNATURE
        + _IHDR
        + _chunk(b"acTL", struct.pack(">II", 2, 0))
        + b"".join(chunks)
        + frame_control(0)
        + _chunk(b"IDAT", _PIXELS)
        + frame_control(1)
        + _chunk(b"fdAT", (2).to_bytes(4, "big") + _PIXELS)
        + _IEND
    )


def _v2_png() -> bytes:
    return _png(_text("chara", _card("v2.json")))


def _replaced(content: bytes, index: int) -> bytes:
    """`content` with the byte at `index` changed to another base64 letter."""
    return content[:index] + (b"B" if content[index : index + 1] == b"A" else b"A") + content[index + 1 :]


def _deep_card(lists: int) -> bytes:
    """A V2 card whose JSON is `lists` + 3 levels deep: the root, `data`, `extensions`, then nested arrays."""
    return (
        '{"spec": "chara_card_v2", "spec_version": "2.0", "data": {"name": "Deep", "extensions": {"deep": '
        + "[" * lists
        + "]" * lists
        + "}}}"
    ).encode("utf-8")


# Parsing


@pytest.mark.parametrize(
    ("name", "spec"), [("v1.json", CardSpec.V1), ("v2.json", CardSpec.V2), ("v3.json", CardSpec.V3)]
)
def test_json_cards_parse_with_their_spec_from_the_content(name: str, spec: CardSpec) -> None:
    content = (FIXTURES / name).read_bytes()
    parsed = parse_card(content)
    assert parsed.spec is spec
    assert parsed.container is CardContainer.JSON
    assert parsed.chunk is None
    assert parsed.source == _card(name)
    assert len(parsed.sha256) == 64


def test_the_typed_view_keeps_every_field_and_unknown_keys() -> None:
    v1 = parse_card((FIXTURES / "v1.json").read_bytes())
    assert v1.spec_version is None
    assert v1.data.name == "Ottavio Remi"
    assert v1.data.mes_example.startswith("<START>")
    # A V1 card fills only its six texts; its other keys stay in the source.
    assert v1.data.extensions == {}
    assert v1.source["talkativeness"] == "0.4"

    v2 = parse_card((FIXTURES / "v2.json").read_bytes())
    assert v2.spec_version == "2.0"
    assert v2.data.character_book is not None
    assert len(v2.data.character_book.entries) == 4
    assert v2.data.character_book.entries[0].model_extra == {
        "insertion_order": 100,
        "case_sensitive": False,
        "priority": 10,
        "id": 1,
        "selective": False,
        "constant": False,
        "position": "before_char",
    }
    assert v2.data.extensions["example_tool"] == {"note": "An unknown extension that must survive.", "level": 3}
    assert v2.data.alternate_greetings[1].startswith("*Livia waves")

    v3 = parse_card((FIXTURES / "v3.json").read_bytes())
    assert v3.data.nickname == "Teo"
    assert [asset.type for asset in v3.data.assets] == ["icon", "background"]
    assert v3.data.creator_notes_multilingual["it"].startswith("Card sintetica")
    assert v3.data.group_only_greetings
    assert v3.data.creation_date == 1764000000


def test_null_fields_read_as_empty() -> None:
    card = _card("v2.json")
    card["data"].update({"creator_notes": None, "tags": None, "extensions": None, "character_book": None})
    data = parse_card(_json(card)).data
    assert (data.creator_notes, data.tags, data.extensions, data.character_book) == ("", [], {}, None)


def test_a_v2_png_is_read_from_its_chara_chunk() -> None:
    content = _v2_png()
    with Image.open(io.BytesIO(content)) as image:
        image.load()
    parsed = parse_card(content)
    assert (parsed.spec, parsed.container, parsed.chunk) == (CardSpec.V2, CardContainer.PNG, CardChunk.CHARA)
    assert parsed.source == _card("v2.json")


@pytest.mark.parametrize("ccv3_first", [True, False])
def test_ccv3_wins_when_a_png_holds_both_chunks(ccv3_first: bool) -> None:
    ccv3, chara = _text("ccv3", _card("v3.json")), _text("chara", _card("v2.json"))
    parsed = parse_card(_png(ccv3, chara) if ccv3_first else _png(chara, ccv3))
    assert (parsed.spec, parsed.chunk) == (CardSpec.V3, CardChunk.CCV3)
    assert parsed.data.name == "Teodoro Brina"


def test_an_apng_is_read_and_named_as_such() -> None:
    content = _apng(_text("ccv3", _card("v3.json")), _text("chara", _card("v2.json")))
    with Image.open(io.BytesIO(content)) as image:
        assert getattr(image, "n_frames", 1) == 2
        image.seek(1)
        image.load()
    parsed = parse_card(content)
    assert (parsed.container, parsed.chunk, parsed.spec) == (CardContainer.APNG, CardChunk.CCV3, CardSpec.V3)

    only_chara = parse_card(_apng(_text("chara", _card("v2.json"))))
    assert (only_chara.container, only_chara.chunk) == (CardContainer.APNG, CardChunk.CHARA)


def test_the_format_comes_from_the_content_whatever_the_blank_start() -> None:
    parsed = parse_card(b"\xef\xbb\xbf\n  " + (FIXTURES / "v2.json").read_bytes())
    assert parsed.spec is CardSpec.V2


def test_nesting_of_64_levels_passes() -> None:
    assert parse_card(_deep_card(61)).data.name == "Deep"


# Refusals: each has its own reason, and the class gives the HTTP answer.

_V2 = (FIXTURES / "v2.json").read_bytes()
_UNKNOWN_SPEC = _json({"spec": "chara_card_v4", "spec_version": "4.0", "data": {"name": "X"}})
_MISSING_SPEC = _json({"data": {"name": "X"}})
_BAD_FIELDS = _json({"spec": "chara_card_v2", "spec_version": "2.0", "data": {"name": "X", "tags": "sea"}})
_BAD_BASE64 = _png(_chunk(b"tEXt", b"chara\0not base64!"))
_BAD_CHUNK_JSON = _png(_chunk(b"tEXt", b"chara\0" + base64.b64encode(b'{"spec": ')))
_HUGE_LENGTH = _v2_png()[:_AFTER_IHDR] + b"\xff\xff\xff\xf0" + _v2_png()[_AFTER_IHDR + 4 :]


@pytest.mark.parametrize(
    ("content", "error", "reason"),
    [
        pytest.param(b" " * (MAX_CARD_BYTES + 1), CardTooLargeError, CardFailure.TOO_LARGE, id="over-64-mb"),
        pytest.param(b"PK\x03\x04" + b"\0" * 26 + b"card.json", CardMediaError, CardFailure.CHARX, id="charx"),
        pytest.param(b"GIF89a\x01\x00\x01\x00", CardMediaError, CardFailure.NOT_A_CARD_FILE, id="gif"),
        pytest.param(b"name: Livia", CardMediaError, CardFailure.NOT_A_CARD_FILE, id="plain-text"),
        pytest.param(_HUGE_LENGTH, CardInvalidError, CardFailure.CHUNK_LENGTH, id="chunk-length"),
        pytest.param(_v2_png()[:-20], CardInvalidError, CardFailure.CHUNK_LENGTH, id="truncated-png"),
        pytest.param(_replaced(_v2_png(), _AFTER_IHDR + 20), CardInvalidError, CardFailure.CHUNK_CRC, id="chunk-crc"),
        pytest.param(PNG_SIGNATURE + _IEND, CardInvalidError, CardFailure.BAD_PNG, id="no-ihdr"),
        pytest.param(_png(), CardInvalidError, CardFailure.NO_CARD_CHUNK, id="no-card-chunk"),
        pytest.param(_BAD_BASE64, CardInvalidError, CardFailure.BAD_BASE64, id="bad-base64"),
        pytest.param(_BAD_CHUNK_JSON, CardInvalidError, CardFailure.BAD_JSON, id="bad-json-in-chunk"),
        pytest.param(_V2[:-10], CardInvalidError, CardFailure.BAD_JSON, id="bad-json"),
        pytest.param(b'{"spec": NaN}', CardInvalidError, CardFailure.BAD_JSON, id="nan"),
        pytest.param(_deep_card(62), CardInvalidError, CardFailure.TOO_DEEP, id="nesting-65"),
        pytest.param(_deep_card(100_000), CardInvalidError, CardFailure.TOO_DEEP, id="nesting-100003"),
        pytest.param(_UNKNOWN_SPEC, CardInvalidError, CardFailure.UNKNOWN_SPEC, id="unknown-spec"),
        pytest.param(_MISSING_SPEC, CardInvalidError, CardFailure.MISSING_SPEC, id="missing-spec"),
        pytest.param(b"[1, 2]", CardInvalidError, CardFailure.MISSING_SPEC, id="not-an-object"),
        pytest.param(_BAD_FIELDS, CardInvalidError, CardFailure.BAD_FIELDS, id="bad-fields"),
    ],
)
def test_refused_files_get_distinct_errors(content: bytes, error: type[CardRefusedError], reason: CardFailure) -> None:
    with pytest.raises(error) as refused:
        parse_card(content)
    assert refused.value.reason is reason
    assert refused.value.status == {CardTooLargeError: 413, CardMediaError: 415, CardInvalidError: 422}[error]


def test_every_reason_has_its_own_detail() -> None:
    details = {CardRefusedError(reason).detail for reason in CardFailure}
    assert len(details) == len(CardFailure)


def test_a_field_of_the_wrong_type_is_named_by_its_source_path() -> None:
    card = _card("v2.json")
    card["data"]["character_book"]["entries"][2]["keys"] = "logbook"
    with pytest.raises(CardInvalidError) as refused:
        parse_card(_json(card))
    assert "data.character_book.entries[2].keys" in refused.value.detail
    assert refused.value.code == "IMPORT_INVALID"


def test_a_broken_ccv3_chunk_is_refused_rather_than_skipped() -> None:
    content = _png(_chunk(b"tEXt", b"ccv3\0###"), _text("chara", _card("v2.json")))
    with pytest.raises(CardInvalidError) as refused:
        parse_card(content)
    assert refused.value.reason is CardFailure.BAD_BASE64


# Content guard (unit part of T-CRD-04)


def _blocked_path(content: bytes) -> str:
    with pytest.raises(ContentPolicyBlockedError) as refused:
        parse_card(content)
    assert refused.value.code == "CONTENT_POLICY_BLOCKED"
    assert "child" not in refused.value.detail
    return refused.value.detail


@pytest.mark.parametrize(
    ("path", "keys"),
    [
        ("data.character_book.entries[3].content", ("data", "character_book", "entries", 3, "content")),
        ("data.extensions.example_tool.note", ("data", "extensions", "example_tool", "note")),
        ("data.system_prompt", ("data", "system_prompt")),
        ("data.alternate_greetings[0]", ("data", "alternate_greetings", 0)),
    ],
)
def test_a_minor_in_any_string_is_refused_with_its_source_path(path: str, keys: tuple[str | int, ...]) -> None:
    card = _card("v2.json")
    target: Any = card
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = "The keeper's helper is a child."
    assert f"«{path}»" in _blocked_path(_json(card))
    assert f"«{path}»" in _blocked_path(_png(_text("chara", card)))


def test_unused_fields_of_v1_and_v3_cards_are_checked_too() -> None:
    v1 = _card("v1.json")
    v1["talkativeness"] = "Written for a child."
    assert "«talkativeness»" in _blocked_path(_json(v1))

    v3 = _card("v3.json")
    v3["data"]["group_only_greetings"] = ["She is twelve years old."]
    assert "«data.group_only_greetings[0]»" in _blocked_path(_apng(_text("ccv3", v3)))


def test_data_uris_are_not_checked_but_text_that_starts_like_one_is() -> None:
    v3 = _card("v3.json")
    v3["data"]["assets"][0]["uri"] = "data:text/plain;base64,bambina"
    assert parse_card(_json(v3)).data.assets[0].uri.endswith("bambina")

    v3["data"]["description"] = "data: bambina"
    assert "«data.description»" in _blocked_path(_json(v3))


def test_the_guard_runs_after_the_structure_is_valid() -> None:
    card = _card("v2.json")
    card["data"]["description"] = "A child."
    card["data"]["tags"] = "sea"
    with pytest.raises(CardInvalidError):
        parse_card(_json(card))


# Pillow


def test_the_parser_leaves_pillow_alone() -> None:
    tree = ast.parse(Path(tavern_card.__file__).read_text(encoding="utf-8"))
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(module and module.split(".")[0] == "PIL" for module in modules)

    limits = (Image.MAX_IMAGE_PIXELS, PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY)
    parse_card(_apng(_text("ccv3", _card("v3.json"))))
    assert limits == (Image.MAX_IMAGE_PIXELS, PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY)
