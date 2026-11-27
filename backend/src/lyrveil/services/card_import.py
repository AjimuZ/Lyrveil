"""CardImportService: reads a character card file into a `card_imports` row, serves and discards it, and expires the
ones nobody saved (ADR 0047; docs/character-system.md, Character cards).

An import parses the file with lyrveil.domain.tavern_card, stores the parsed card unchanged with the file's SHA-256, its
format and the time, with the direct draft and the import report of lyrveil.domain.card_mapping, and stages the avatar
of a PNG card through the upload path, whose re-encode removes every chunk. It never writes a character and never
fetches a remote `uri` of the card: nothing here opens a connection. Only `save` writes a character, in the one
transaction that links the import's source, avatar and scenarios to it (P10.4) and turns its `character_book` into the
character's own lore book (P12.4).
"""

import base64
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.domain.assets import StoredAsset
from lyrveil.domain.card_imports import (
    IMPORT_LIFETIME,
    READY,
    SAVED,
    ConversionState,
    StoredCardImport,
    StoredCharacterSource,
)
from lyrveil.domain.card_mapping import ScenarioProposal, map_card
from lyrveil.domain.characters import CharacterSummary
from lyrveil.domain.errors import ConflictError, NotFoundError, PayloadTooLargeError, UnsupportedMediaError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.tavern_card import (
    SPEC_V2,
    SPEC_V3,
    CardContainer,
    CardFailure,
    CardInvalidError,
    CardSpec,
    card_data,
    parse_card,
)
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.media import images
from lyrveil.media.images import EncodedImage
from lyrveil.media.store import MediaStore
from lyrveil.persistence import card_imports as repository
from lyrveil.persistence import characters as character_repository
from lyrveil.services.assets import CharacterAssetService
from lyrveil.services.card_roundtrip import EmbeddedLyrveil, read_embedded
from lyrveil.services.card_rules import validate_card
from lyrveil.services.characters import CharacterService
from lyrveil.services.lore import LoreService
from lyrveil.services.lorebook import character_book_of, convert_book
from lyrveil.services.scenarios import ScenarioService

_NO_IMPORT = "L'importazione non esiste o è scaduta."
_NO_CHARACTER = "Il personaggio non esiste."
_NO_SOURCE = "Il personaggio non è stato importato da una card."
_SAVED_IMPORT = "L'importazione è già stata salvata come personaggio."
_EXPIRED_IMPORT = "L'importazione è scaduta e non può più essere salvata."
# A base64 data URI, with any media type and parameters; group 1 is the base64 text.
_DATA_URI = re.compile(r"data:[^,;]+(?:;[^,;]*)*;base64,([A-Za-z0-9+/]*={0,2})")

_FORMATS = {CardSpec.V1: "chara_card_v1", CardSpec.V2: SPEC_V2, CardSpec.V3: SPEC_V3}


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CardPreview:
    """The source fields that no draft uses, for the review screen. `greeting_count` counts `first_mes` when it is not
    empty plus every alternate greeting."""

    name: str
    nickname: str
    creator: str
    version: str
    tags: list[str]
    creator_notes: str
    system_prompt: str
    post_history_instructions: str
    greeting_count: int
    has_avatar: bool
    has_lorebook: bool


@dataclass(frozen=True)
class ImportView:
    """A stored import with its preview, the Lyrveil card embedded in the file when it has a usable one, and, once a
    conversion was started, the state of its job."""

    stored: StoredCardImport
    preview: CardPreview
    conversion: ConversionState | None = None
    embedded: EmbeddedLyrveil | None = None


def _main_icon(source: dict[str, Any]) -> bytes | None:
    """Returns the bytes of the V3 asset of type `icon` named `main` in a stored card `source` when its uri is a base64
    data URI, else None. Only the first such asset counts; a remote or `ccdefault:` uri is never fetched."""
    for asset in card_data(source).assets:
        if asset.type == "icon" and asset.name == "main":
            matched = _DATA_URI.fullmatch(asset.uri.strip())
            if matched is None:
                return None
            try:
                return base64.b64decode(matched.group(1), validate=True)
            except ValueError:
                return None
    return None


def build_preview(source: dict[str, Any], *, has_avatar: bool) -> CardPreview:
    """Returns the preview of a stored card `source`, which already passed the parser."""
    data = card_data(source)
    return CardPreview(
        name=data.name,
        nickname=data.nickname,
        creator=data.creator,
        version=data.character_version,
        tags=list(data.tags),
        creator_notes=data.creator_notes,
        system_prompt=data.system_prompt,
        post_history_instructions=data.post_history_instructions,
        greeting_count=(1 if data.first_mes else 0) + len(data.alternate_greetings),
        has_avatar=has_avatar,
        has_lorebook=data.character_book is not None,
    )


class CardImportService:
    """Imports on `engine`, with staged avatars in `media`. `clock` returns the aware current time."""

    def __init__(self, engine: Engine, media: MediaStore, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._media = media
        self._clock = clock

    def import_file(self, content: bytes) -> ImportView:
        """Returns the new `ready` import made from the bytes of an uploaded card file.

        The card is parsed and checked first (parse_card) and mapped to the direct draft with its report (map_card,
        which needs no model). A card with a `character_book` adds `lore` to the report: what the save will make of the
        book, stemmed in the draft's language (convert_book). Then the avatar of a PNG card is re-encoded through the
        upload path (images.prepare_upload: 15 MB and 8192 pixels, one frame, no chunk survives) and staged; the row
        is written last. Raises what parse_card raises (CardRefusedError, ContentPolicyBlockedError naming the source
        path) and what prepare_upload raises (PayloadTooLargeError, UnsupportedMediaError). In every refusal no row and
        no file is left behind. The card is only read: no remote `uri` is fetched, and no character is written.

        A card with `extensions.lyrveil` also has its embedded card read (read_embedded) before anything is stored: it
        goes through validate_card, so when its `text_sha256` matches the file's text and a card rule or the content
        guard refuses it, the whole import is refused (ImportInvalidError, ContentPolicyBlockedError).
        """
        parsed = parse_card(content)
        embedded = read_embedded(parsed.data, refuse=True)
        try:
            source_json = json.dumps(parsed.source, ensure_ascii=True, allow_nan=False)
        except ValueError as error:
            # A number such as 1e999 reads as infinity, which JSON text cannot hold.
            raise CardInvalidError(CardFailure.BAD_JSON) from error
        mapping = map_card(parsed.data, prefix="" if parsed.spec is CardSpec.V1 else "data")
        report = mapping.report_json()
        book = character_book_of(parsed.source)
        if book is not None:
            # The draft's language: the save stems the keys again with the character's language.
            report["lore"] = convert_book(book, mapping.draft.identity.primary_language, parsed.data.name).report_json()
        # ASCII escapes keep a lone surrogate of the card's text storable, as for the source.
        draft_json = json.dumps(mapping.draft_json(), ensure_ascii=True)
        report_json = json.dumps(report, ensure_ascii=True)
        avatar = None if parsed.container is CardContainer.JSON else images.prepare_upload(content, None)
        import_id = new_id()
        now = self._clock()
        staged = None if avatar is None else self._media.stage_import_avatar(import_id, avatar.data)
        stored = StoredCardImport(
            id=import_id,
            status=READY,
            format=_FORMATS[parsed.spec],
            container="json" if parsed.container is CardContainer.JSON else "png",
            source_json=source_json,
            source_sha256=parsed.sha256,
            direct_draft_json=draft_json,
            report_json=report_json,
            conversion_job_id=None,
            avatar_staging_path=staged,
            character_id=None,
            created_at=to_utc_text(now),
            expires_at=to_utc_text(now + IMPORT_LIFETIME),
        )
        try:
            write(self._engine, lambda connection: repository.insert(connection, stored))
        except BaseException:
            if staged is not None:
                self._media.discard_import_avatar(import_id)
            raise
        return self._view(stored, embedded)

    def save(self, import_id: str, raw_card: Mapping[str, Any], *, create_scenarios: bool) -> CharacterSummary:
        """Returns the new active character that `raw_card`, the reviewed draft, makes of import `import_id`: the normal
        create with every card rule (validate_card, the content guard), at version 1 with actor `import`.

        One transaction writes the character, its version, the `character_sources` row (the stored card unchanged),
        the character's own lore book made from the card's `character_book` when it has one (lyrveil.services.lorebook,
        keys stemmed with the card's primary language), the primary avatar and, when `create_scenarios`, a scenario for
        each greeting of the direct mapping
        (`group_only_greetings` is never one), and sets the import `saved`. The avatar is the staged image of a PNG
        card, or for a JSON card the V3 `icon` asset named `main` when its uri is a data URI of a PNG, JPEG or WebP
        that decodes; an icon that does not decode gives no avatar rather than refusing the save. The staged file is
        removed after the commit, and the asset files if the transaction fails.

        Raises NotFoundError for an unknown import, ConflictError for one that was already saved or has expired (the
        check repeats inside the transaction, so two saves of the same import never both win), ValidationFailedError
        for what validate_card refuses (a missing or under-18 age among them) and ContentPolicyBlockedError when a text
        describes a minor. Nothing is written in those cases.
        """
        stored = read(self._engine, lambda connection: repository.get(connection, import_id))
        if stored is None:
            raise NotFoundError(_NO_IMPORT)
        self._require_savable(stored)
        card = validate_card(raw_card)
        image = self._avatar_image(stored)
        proposals = self._scenario_proposals(stored) if create_scenarios else []
        assets = CharacterAssetService(self._engine, self._media, self._clock)
        written: list[StoredAsset] = []

        def within(connection: Connection, character_id: str, now: str) -> None:
            current = repository.get(connection, import_id)
            if current is None:
                raise NotFoundError(_NO_IMPORT)
            self._require_savable(current, now)
            repository.insert_source(
                connection,
                StoredCharacterSource(
                    id=new_id(),
                    character_id=character_id,
                    format=current.format,
                    container=current.container,
                    source_json=current.source_json,
                    source_sha256=current.source_sha256,
                    imported_at=now,
                ),
            )
            book = character_book_of(json.loads(current.source_json))
            if book is not None:
                identity = card.identity
                imported = convert_book(book, identity.primary_language, identity.first_name)
                LoreService.store_own_book_in(connection, character_id, imported, now)
            if image is not None:
                written.append(assets.store_primary_avatar_in(connection, character_id, image, now=now))
            for proposal in proposals:
                fields = {
                    "title": proposal.title,
                    "description": proposal.description,
                    "opening_message": proposal.opening_message,
                    "character_id": character_id,
                }
                ScenarioService.create_in(connection, fields, now)
            repository.mark_saved(connection, import_id, character_id)

        try:
            created = CharacterService(self._engine, self._clock, self._media).create_from_import(card, within)
        except BaseException:
            for asset in written:
                self._media.delete(asset.file_path)
                self._media.delete(asset.thumb_path)
            raise
        self._media.discard_import_avatar(import_id)
        return created

    def get(self, import_id: str) -> ImportView:
        """Returns the import with its preview and the state of its conversion job, if one was started. Raises
        NotFoundError for an unknown id and for an import that expired without being saved, even before the daily
        clean-up removes its row."""
        stored = self._live(import_id)
        view = self._view(stored, read_embedded(card_data(json.loads(stored.source_json)), refuse=False))
        if stored.conversion_job_id is None:
            return view
        job_id = stored.conversion_job_id
        conversion = read(self._engine, lambda c: self._conversion(c, job_id))
        return ImportView(view.stored, view.preview, conversion, view.embedded)

    def source_json(self, import_id: str) -> str:
        """Returns the parsed card of the import as the JSON text that was stored. Raises NotFoundError as `get`."""
        return self._live(import_id).source_json

    def character_source_json(self, character_id: str) -> str:
        """Returns the stored source card of an imported character as JSON text. Raises NotFoundError for an unknown
        character and for one that was not imported from a card."""

        def work(connection: Connection) -> str:
            if character_repository.get(connection, character_id) is None:
                raise NotFoundError(_NO_CHARACTER)
            source = repository.source_of_character(connection, character_id)
            if source is None:
                raise NotFoundError(_NO_SOURCE)
            return source.source_json

        return read(self._engine, work)

    def discard(self, import_id: str) -> None:
        """Deletes an unsaved import and its staged avatar. Raises NotFoundError for an unknown id and ConflictError
        for an import that was saved as a character. The row goes first and the file after the commit, so a crash
        leaves at most an orphan file, never a row without its file."""

        def work(connection: Connection) -> StoredCardImport:
            stored = repository.get(connection, import_id)
            if stored is None:
                raise NotFoundError(_NO_IMPORT)
            if stored.status == SAVED:
                raise ConflictError(_SAVED_IMPORT)
            repository.delete(connection, import_id)
            return stored

        removed = write(self._engine, work)
        if removed.avatar_staging_path is not None:
            self._media.discard_import_avatar(import_id)

    def expire(self) -> int:
        """Deletes every import that is not saved and has expired, with its staged avatar, and returns how many it
        removed. Startup and the daily backup call it."""
        now = to_utc_text(self._clock())
        ids = write(self._engine, lambda connection: repository.delete_expired(connection, now))
        for import_id in ids:
            self._media.discard_import_avatar(import_id)
        return len(ids)

    def _live(self, import_id: str) -> StoredCardImport:
        now = to_utc_text(self._clock())
        stored = read(self._engine, lambda connection: repository.get(connection, import_id))
        if stored is None or (stored.status != SAVED and stored.expires_at <= now):
            raise NotFoundError(_NO_IMPORT)
        return stored

    def _require_savable(self, stored: StoredCardImport, now: str | None = None) -> None:
        """Raises ConflictError for an import that was saved already, or that expired unsaved at `now` (the clock's
        time when None)."""
        if stored.status == SAVED:
            raise ConflictError(_SAVED_IMPORT)
        if stored.expires_at <= (now or to_utc_text(self._clock())):
            raise ConflictError(_EXPIRED_IMPORT)

    def _avatar_image(self, stored: StoredCardImport) -> EncodedImage | None:
        """Returns the avatar the import will give its character, or None: the staged image of a PNG card, which the
        upload path already re-encoded without chunks, else the V3 `icon` asset `main` of a JSON card."""
        if stored.container == "png":
            data = self._media.read_import_avatar(stored.id)
        else:
            data = _main_icon(json.loads(stored.source_json))
        if data is None:
            return None
        try:
            return images.prepare_upload(data, None)
        except (PayloadTooLargeError, UnsupportedMediaError):
            return None

    @staticmethod
    def _scenario_proposals(stored: StoredCardImport) -> list[ScenarioProposal]:
        """Returns the scenarios of the import's direct mapping; none for a row stored before P10.2b."""
        if stored.direct_draft_json is None:
            return []
        return [ScenarioProposal(**item) for item in json.loads(stored.direct_draft_json)["scenarios"]]

    @staticmethod
    def _conversion(connection: Connection, job_id: str) -> ConversionState | None:
        """Returns the state of job `job_id`, or None when the job row is gone."""
        job = JobQueue().get(connection, job_id)
        if job is None:
            return None
        return ConversionState(
            job_id=job.id,
            status=job.status.value,
            error_code=job.error_code,
            error_detail=job.error_detail,
            result=job.result,
        )

    @staticmethod
    def _view(stored: StoredCardImport, embedded: EmbeddedLyrveil | None) -> ImportView:
        preview = build_preview(json.loads(stored.source_json), has_avatar=stored.avatar_staging_path is not None)
        return ImportView(stored=stored, preview=preview, embedded=embedded)
