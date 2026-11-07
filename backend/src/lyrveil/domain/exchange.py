"""The `lyrveil.character` exchange format of docs/character-system.md (Exchange format): the file's structure, with no
database. The JSON Schema the API publishes is generated from ExchangeFile, so the schema and the import cannot drift.
"""

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from lyrveil.domain.assets import ASSET_KINDS
from lyrveil.domain.card import CharacterCard

FORMAT_NAME = "lyrveil.character"
FORMAT_VERSION = 1
FILE_SUFFIX = ".lyrveil-character.json"
MAX_IMPORT_BYTES = 64 * 1024 * 1024

# The order the documented example shows; a file is written in this order.
FILE_KEYS = ("format", "format_version", "exported_at", "generator", "character", "assets")

AssetKind = Literal["avatar", "profile", "scene", "variant", "reference"]
MediaType = Literal["image/png", "image/jpeg", "image/webp"]

assert set(get_args(AssetKind)) == ASSET_KINDS, "the format's asset kinds must be the stored ones"


class _Strict(BaseModel):
    """Unknown fields are refused, so that a typo is not silently lost."""

    model_config = ConfigDict(extra="forbid")


class Generator(_Strict):
    app: str = Field(min_length=1, max_length=40)
    version: str = Field(min_length=1, max_length=40)


class ExchangeAsset(_Strict):
    """An asset of the file. `sha256` is the hex hash of the decoded bytes; `data_base64` is present only in an export
    made with the assets included."""

    kind: AssetKind
    is_primary: bool
    media_type: MediaType
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_base64: str | None = None


class ExchangeEnvelope(_Strict):
    """Everything in the file but the card. The import checks the card first, with the card rules of the service and
    messages that name its fields, and then checks the rest of the file against this model."""

    format: Literal["lyrveil.character"]
    format_version: Literal[1]
    exported_at: str = Field(min_length=1, max_length=40)
    generator: Generator
    assets: list[ExchangeAsset] = []


class ExchangeFile(ExchangeEnvelope):
    """A whole file; the model the published JSON Schema comes from."""

    character: CharacterCard


def json_schema() -> dict[str, Any]:
    """Returns the JSON Schema (draft 2020-12) of a character file, generated from ExchangeFile. Every exported file
    validates against it; it lists the same required fields and closed lists as the import, and refuses unknown fields
    (`additionalProperties: false`). The card's text rules that need the tag vocabulary are not in it: the import
    applies those."""
    schema = ExchangeFile.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://lyrveil.invalid/schemas/lyrveil.character.v1.json",
        **schema,
        "title": "lyrveil.character",
    }
