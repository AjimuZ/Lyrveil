"""CharacterExchangeService: writes a character as a `lyrveil.character` file and creates a new character from one
(docs/character-system.md, Exchange format; ADR 0021).

A file holds the card and its assets, never conversations or memories. An import validates the whole file before it
writes anything, and always creates a new character: it never overwrites one.
"""

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine

from lyrveil import __version__
from lyrveil.domain.characters import CharacterSummary
from lyrveil.domain.errors import (
    ImportInvalidError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaError,
    ValidationFailedError,
)
from lyrveil.domain.exchange import (
    FILE_KEYS,
    FILE_SUFFIX,
    FORMAT_NAME,
    FORMAT_VERSION,
    MAX_IMPORT_BYTES,
    ExchangeAsset,
    ExchangeEnvelope,
    ExchangeFile,
)
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.media import images
from lyrveil.media.images import EncodedImage
from lyrveil.media.store import MediaPathError, MediaStore
from lyrveil.services.assets import CharacterAssetService
from lyrveil.services.card_rules import describe_error, validate_card
from lyrveil.services.characters import CharacterService

_TOO_LARGE = "Il file supera i 64 MB."
_NOT_JSON = "Il file non è un JSON valido."
_NOT_A_CHARACTER = "Il file non è un personaggio di Lyrveil."
_BAD_VERSION = "La versione del formato non è valida."
_NEWER_VERSION = "Il file usa una versione del formato più recente di quella supportata. Aggiorna Lyrveil."
_NO_CARD = "Manca il campo «character»."
_BAD_BASE64 = "Un'immagine del file non è codificata correttamente."
_BAD_HASH = "L'impronta di un'immagine non corrisponde ai suoi dati."
_BAD_PRIMARY = "Il file indica un avatar principale non valido."
_MISSING_FILE = "Un'immagine del personaggio non è disponibile."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _file_name(first_name: str) -> str:
    """Returns an ASCII file name for the export of the character named `first_name`."""
    ascii_name = unicodedata.normalize("NFKD", first_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return f"{slug or 'personaggio'}{FILE_SUFFIX}"


@dataclass(frozen=True)
class ExportedCharacter:
    """The file of an export: `document` is the JSON object in the order of the documented example, and `file_name` an
    ASCII name ending in `.lyrveil-character.json`."""

    document: dict[str, Any]
    file_name: str


@dataclass(frozen=True)
class ImportResult:
    """What an import created. `assets_imported` counts the images stored; `assets_without_data` those the file listed
    without bytes (an export made without assets), which have nothing to import."""

    character: CharacterSummary
    assets_imported: int
    assets_without_data: int


class CharacterExchangeService:
    """Exports and imports characters of `engine`, with their asset files in `media`. `clock` returns the aware current
    time and stamps `exported_at`."""

    def __init__(self, engine: Engine, media: MediaStore, clock: Callable[[], datetime] = _utc_now) -> None:
        self._media = media
        self._clock = clock
        self._characters = CharacterService(engine, clock, media)
        self._assets = CharacterAssetService(engine, media, clock)

    def export(self, character_id: str, include_assets: bool) -> ExportedCharacter:
        """Returns the file of the character: its current card and its live assets, the primary avatar first and the
        others oldest first. Each asset lists its kind, media type and the SHA-256 of its bytes; with `include_assets`
        it also carries the bytes in `data_base64`.

        Raises NotFoundError for an unknown character, or when an asset file is missing and the bytes were asked for.
        """
        stored = self._characters.get(character_id)
        live = self._assets.list_assets(character_id)
        live.sort(key=lambda asset: (not asset.is_primary, asset.created_at, asset.id))
        assets: list[dict[str, Any]] = []
        for asset in live:
            entry: dict[str, Any] = {
                "kind": asset.kind,
                "is_primary": asset.is_primary,
                "media_type": asset.media_type,
                "sha256": asset.sha256,
            }
            if include_assets:
                try:
                    data = self._media.read(asset.file_path)
                except (OSError, MediaPathError):
                    raise NotFoundError(_MISSING_FILE) from None
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                entry["data_base64"] = base64.b64encode(data).decode("ascii")
            assets.append(entry)
        # Validating what is about to be written keeps the export inside the published schema.
        file = ExchangeFile.model_validate(
            {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "exported_at": to_utc_text(self._clock()),
                "generator": {"app": "lyrveil", "version": __version__},
                "character": stored.card,
                "assets": assets,
            }
        )
        dumped = file.model_dump(mode="json")
        for entry in dumped["assets"]:
            if entry["data_base64"] is None:
                del entry["data_base64"]
        return ExportedCharacter({key: dumped[key] for key in FILE_KEYS}, _file_name(stored.card.identity.first_name))

    def import_file(self, data: bytes) -> ImportResult:
        """Creates a new active character at version 1, actor `import`, from the bytes of a character file, and stores
        its images. The whole file is validated first, so a refusal writes nothing.

        Raises PayloadTooLargeError over 64 MB, and ImportInvalidError, with an Italian detail, for text that is not
        JSON, another or a newer format, a card the card rules refuse (an age under 18, an unknown field...), an
        unknown field in the file, an image that is not valid base64, does not match its hash or is not a PNG, JPEG or
        WebP that decodes, or an avatar flagged primary twice. Raises ContentPolicyBlockedError, not ImportInvalidError,
        when a text of the card describes a minor. An image whose bytes carry metadata is stored without it, so its
        hash then differs from the recorded one.
        """
        if len(data) > MAX_IMPORT_BYTES:
            raise PayloadTooLargeError(_TOO_LARGE)
        raw = self._parse(data)
        try:
            card = validate_card(raw["character"])
        except ValidationFailedError as error:
            raise ImportInvalidError(error.detail) from None
        try:
            envelope = ExchangeEnvelope.model_validate({key: value for key, value in raw.items() if key != "character"})
        except ValidationError as error:
            raise ImportInvalidError(describe_error(error)) from None
        prepared, without_data = self._prepare_assets(envelope.assets)

        created = self._characters.create_imported(card)
        try:
            for asset, image in prepared:
                self._assets.import_asset(created.id, asset.kind, image, primary=asset.is_primary)
        except BaseException:
            self._characters.purge(created.id, created.first_name)
            raise
        return ImportResult(character=created, assets_imported=len(prepared), assets_without_data=without_data)

    @staticmethod
    def _parse(data: bytes) -> dict[str, Any]:
        """Returns the top-level object of a character file after the checks that come before any field is read: JSON,
        `format`, `format_version` and a `character` object."""
        try:
            raw = json.loads(data)
        except (ValueError, RecursionError):
            raise ImportInvalidError(_NOT_JSON) from None
        if not isinstance(raw, dict) or raw.get("format") != FORMAT_NAME:
            raise ImportInvalidError(_NOT_A_CHARACTER)
        version = raw.get("format_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ImportInvalidError(_BAD_VERSION)
        if version > FORMAT_VERSION:
            raise ImportInvalidError(_NEWER_VERSION)
        if not isinstance(raw.get("character"), dict):
            raise ImportInvalidError(_NO_CARD)
        return raw

    @staticmethod
    def _prepare_assets(assets: list[ExchangeAsset]) -> tuple[list[tuple[ExchangeAsset, EncodedImage]], int]:
        """Returns the images of the file ready to store, with the count of assets that carry no bytes. Raises
        ImportInvalidError as `import_file` documents."""
        if sum(1 for asset in assets if asset.is_primary) > 1 or any(
            asset.is_primary and asset.kind != "avatar" for asset in assets
        ):
            raise ImportInvalidError(_BAD_PRIMARY)
        prepared: list[tuple[ExchangeAsset, EncodedImage]] = []
        without_data = 0
        for asset in assets:
            if asset.data_base64 is None:
                without_data += 1
                continue
            try:
                data = base64.b64decode(asset.data_base64, validate=True)
            except (binascii.Error, ValueError):
                raise ImportInvalidError(_BAD_BASE64) from None
            if hashlib.sha256(data).hexdigest() != asset.sha256:
                raise ImportInvalidError(_BAD_HASH)
            try:
                prepared.append((asset, images.prepare_import(data, asset.media_type)))
            except (UnsupportedMediaError, PayloadTooLargeError) as error:
                raise ImportInvalidError(f"Un'immagine del file non è valida: {error.detail}") from None
        return prepared, without_data
