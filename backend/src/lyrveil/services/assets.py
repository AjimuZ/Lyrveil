"""CharacterAssetService: uploads, lists, crops and deletes the pictures of a character, keeps its single primary
avatar, and finds the files the media routes serve (docs/character-system.md, Avatars and images).

Files are written before their row and removed after their row is gone, so a crash leaves at most an orphan file,
never a row without its file.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, Engine

from lyrveil.domain.assets import ASSET_KINDS, UPLOAD_KINDS, StoredAsset
from lyrveil.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.media import images
from lyrveil.media.images import EncodedImage
from lyrveil.media.store import MediaPathError, MediaStore
from lyrveil.persistence import assets as repository
from lyrveil.persistence import characters as character_repository

logger = get_logger(__name__)

_NO_CHARACTER = "Il personaggio non esiste."
_NO_ASSET = "L'immagine non esiste."
_BAD_KIND = "Il tipo di immagine non è valido."
_NOT_AVATAR = "Solo un avatar può diventare l'avatar principale."
_PRIMARY_DELETE = "Scegli prima un altro avatar principale, poi elimina questo."


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CharacterAssetService:
    """Reads and writes the assets of `engine`, with their files in `media`. `clock` returns the aware current time."""

    def __init__(self, engine: Engine, media: MediaStore, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._media = media
        self._clock = clock

    def upload(self, character_id: str, kind: str, data: bytes, declared_type: str | None) -> StoredAsset:
        """Returns the new asset made from the uploaded `data`, re-encoded without metadata, with its thumbnail.

        The first live avatar of a character becomes its primary avatar; later ones do not. Raises
        ValidationFailedError for a kind outside UPLOAD_KINDS, NotFoundError for an unknown character, and the
        PayloadTooLargeError or UnsupportedMediaError of images.prepare_upload. Nothing is stored in those cases.
        """
        if kind not in UPLOAD_KINDS:
            raise ValidationFailedError(_BAD_KIND)
        self._require_character(character_id)
        return self._store(character_id, kind, "upload", images.prepare_upload(data, declared_type), crop=None)

    def crop(self, character_id: str, asset_id: str, *, x: int, y: int, width: int, height: int) -> StoredAsset:
        """Returns a new asset of the same kind holding the rectangle of `asset_id` scaled to 512 x 512 (see
        images.crop). The original stays. The crop becomes primary only as the first avatar would.

        Raises NotFoundError for an unknown character or an asset that is not one of its live assets, and
        ValidationFailedError when the rectangle leaves the image.
        """
        source = self._owned(character_id, asset_id)
        image_format = images.FORMATS[source.media_type]
        cropped = images.crop(self._media.read(source.file_path), image_format, x=x, y=y, width=width, height=height)
        rectangle: dict[str, int | str] = {"source_asset_id": source.id, "x": x, "y": y}
        rectangle.update(width=width, height=height)
        return self._store(character_id, source.kind, source.source, cropped, crop=rectangle)

    def set_primary(self, character_id: str, asset_id: str) -> StoredAsset:
        """Makes `asset_id` the only primary avatar of the character and returns it; the previous one stays as a
        variant. Raises NotFoundError as `crop` does and ValidationFailedError when the asset is not an avatar."""
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> StoredAsset:
            asset = self._owned_in(connection, character_id, asset_id)
            if asset.kind != "avatar":
                raise ValidationFailedError(_NOT_AVATAR)
            repository.set_primary(connection, character_id=character_id, asset_id=asset_id, now=now)
            changed = repository.get(connection, asset_id)
            assert changed is not None
            return changed

        return write(self._engine, work)

    def delete(self, character_id: str, asset_id: str) -> None:
        """Marks the asset deleted, then removes its file and thumbnail.

        Raises NotFoundError as `crop` does, and ConflictError for the primary avatar until another one is chosen.
        Both paths are checked inside the transaction, so a path outside the media root (MediaPathError) leaves the
        row as it was.
        """
        now = to_utc_text(self._clock())

        def work(connection: Connection) -> StoredAsset:
            asset = self._owned_in(connection, character_id, asset_id)
            if asset.is_primary:
                raise ConflictError(_PRIMARY_DELETE)
            self._media.resolve(asset.file_path)
            self._media.resolve(asset.thumb_path)
            repository.mark_deleted(connection, asset_id=asset_id, now=now)
            return asset

        deleted = write(self._engine, work)
        self._media.delete(deleted.file_path)
        self._media.delete(deleted.thumb_path)

    def list_assets(self, character_id: str, kind: str | None = None) -> list[StoredAsset]:
        """Returns the live assets of the character, of `kind` when given, newest first. Raises NotFoundError for an
        unknown character and ValidationFailedError for a kind outside ASSET_KINDS."""
        if kind is not None and kind not in ASSET_KINDS:
            raise ValidationFailedError(_BAD_KIND)

        def work(connection: Connection) -> list[StoredAsset] | None:
            if character_repository.get(connection, character_id) is None:
                return None
            return repository.list_for(connection, character_id, kind)

        found = read(self._engine, work)
        if found is None:
            raise NotFoundError(_NO_CHARACTER)
        return found

    def media_file(self, asset_id: str, *, thumbnail: bool) -> tuple[Path, str]:
        """Returns the real path and media type of the file of a live asset, or of its WebP thumbnail.

        The path comes from the asset row, never from the request. Raises NotFoundError for an id that names no live
        asset, whose file is missing, or whose stored path leaves the media root; that last case is logged as
        `media_read_refused`, since only a damaged or tampered row can hold such a path.
        """
        asset = read(self._engine, lambda connection: repository.get(connection, asset_id))
        if asset is None:
            raise NotFoundError(_NO_ASSET)
        try:
            path = self._media.resolve(asset.thumb_path if thumbnail else asset.file_path)
        except MediaPathError:
            logger.warning("media_read_refused")
            raise NotFoundError(_NO_ASSET) from None
        if not path.is_file():
            raise NotFoundError(_NO_ASSET)
        return path, "image/webp" if thumbnail else asset.media_type

    def _require_character(self, character_id: str) -> None:
        if read(self._engine, lambda connection: character_repository.get(connection, character_id)) is None:
            raise NotFoundError(_NO_CHARACTER)

    def _owned(self, character_id: str, asset_id: str) -> StoredAsset:
        return read(self._engine, lambda connection: self._owned_in(connection, character_id, asset_id))

    @staticmethod
    def _owned_in(connection: Connection, character_id: str, asset_id: str) -> StoredAsset:
        """Returns the live asset `asset_id` of the character. Raises NotFoundError for an unknown character, and for
        an asset that is deleted or belongs to another character, so ids of other characters reveal nothing."""
        if character_repository.get(connection, character_id) is None:
            raise NotFoundError(_NO_CHARACTER)
        asset = repository.get(connection, asset_id)
        if asset is None or asset.character_id != character_id:
            raise NotFoundError(_NO_ASSET)
        return asset

    def _store(
        self, character_id: str, kind: str, source: str, image: EncodedImage, *, crop: dict[str, int | str] | None
    ) -> StoredAsset:
        """Writes the files of `image` below `assets/<character id>/`, named by a new asset id, then its row. When the
        row cannot be written (the character was purged meanwhile, for one) the files are removed again."""
        asset_id = new_id()
        folder = f"assets/{character_id}"
        file_path = f"{folder}/{asset_id}.{image.format.extension}"
        thumb_path = f"{folder}/{asset_id}.thumb.webp"
        now = to_utc_text(self._clock())
        self._media.save(file_path, image.data)
        try:
            self._media.save(thumb_path, image.thumbnail)

            def work(connection: Connection) -> StoredAsset:
                if character_repository.get(connection, character_id) is None:
                    raise NotFoundError(_NO_CHARACTER)
                asset = StoredAsset(
                    id=asset_id,
                    character_id=character_id,
                    kind=kind,
                    source=source,
                    file_path=file_path,
                    thumb_path=thumb_path,
                    media_type=image.format.media_type,
                    width=image.width,
                    height=image.height,
                    byte_size=len(image.data),
                    sha256=image.sha256,
                    crop=dict(crop) if crop is not None else None,
                    is_primary=False,
                    created_at=now,
                )
                repository.insert(connection, asset)
                if kind == "avatar" and not repository.has_primary(connection, character_id):
                    repository.set_primary(connection, character_id=character_id, asset_id=asset_id, now=now)
                stored = repository.get(connection, asset_id)
                assert stored is not None
                return stored

            return write(self._engine, work)
        except BaseException:
            self._media.delete(file_path)
            self._media.delete(thumb_path)
            raise
