"""CardImportService: reads a character card file into a `card_imports` row, serves and discards it, and expires the
ones nobody saved (ADR 0047; docs/character-system.md, Character cards).

An import parses the file with lyrveil.domain.tavern_card, stores the parsed card unchanged with the file's SHA-256, its
format and the time, with the direct draft and the import report of lyrveil.domain.card_mapping, and stages the avatar
of a PNG card through the upload path, whose re-encode removes every chunk. It never writes a character and never
fetches a remote `uri` of the card: nothing here opens a connection.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from lyrveil.domain.card_imports import IMPORT_LIFETIME, READY, SAVED, ConversionState, StoredCardImport
from lyrveil.domain.card_mapping import map_card
from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.tavern_card import (
    SPEC_V2,
    SPEC_V3,
    CardContainer,
    CardData,
    CardFailure,
    CardInvalidError,
    CardSpec,
    parse_card,
)
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import read, write
from lyrveil.media import images
from lyrveil.media.store import MediaStore
from lyrveil.persistence import card_imports as repository
from lyrveil.persistence import characters as character_repository

_NO_IMPORT = "L'importazione non esiste o è scaduta."
_NO_CHARACTER = "Il personaggio non esiste."
_NO_SOURCE = "Il personaggio non è stato importato da una card."
_SAVED_IMPORT = "L'importazione è già stata salvata come personaggio."

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
    """A stored import with its preview and, once a conversion was started, the state of its job."""

    stored: StoredCardImport
    preview: CardPreview
    conversion: ConversionState | None = None


def card_data(source: dict[str, Any]) -> CardData:
    """Returns the typed fields of a stored card `source`, which already passed the parser: the `data` of a V2 or V3
    envelope, or the card itself for V1."""
    return CardData.model_validate(source if "spec" not in source else source["data"])


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
        which needs no model), then the avatar of a PNG card is re-encoded through the
        upload path (images.prepare_upload: 15 MB and 8192 pixels, one frame, no chunk survives) and staged; the row
        is written last. Raises what parse_card raises (CardRefusedError, ContentPolicyBlockedError naming the source
        path) and what prepare_upload raises (PayloadTooLargeError, UnsupportedMediaError). In every refusal no row and
        no file is left behind. The card is only read: no remote `uri` is fetched, and no character is written.
        """
        parsed = parse_card(content)
        try:
            source_json = json.dumps(parsed.source, ensure_ascii=True, allow_nan=False)
        except ValueError as error:
            # A number such as 1e999 reads as infinity, which JSON text cannot hold.
            raise CardInvalidError(CardFailure.BAD_JSON) from error
        mapping = map_card(parsed.data, prefix="" if parsed.spec is CardSpec.V1 else "data")
        # ASCII escapes keep a lone surrogate of the card's text storable, as for the source.
        draft_json = json.dumps(mapping.draft_json(), ensure_ascii=True)
        report_json = json.dumps(mapping.report_json(), ensure_ascii=True)
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
        return self._view(stored)

    def get(self, import_id: str) -> ImportView:
        """Returns the import with its preview and the state of its conversion job, if one was started. Raises
        NotFoundError for an unknown id and for an import that expired without being saved, even before the daily
        clean-up removes its row."""
        stored = self._live(import_id)
        view = self._view(stored)
        if stored.conversion_job_id is None:
            return view
        job_id = stored.conversion_job_id
        return ImportView(view.stored, view.preview, read(self._engine, lambda c: self._conversion(c, job_id)))

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
    def _view(stored: StoredCardImport) -> ImportView:
        preview = build_preview(json.loads(stored.source_json), has_avatar=stored.avatar_staging_path is not None)
        return ImportView(stored=stored, preview=preview)
