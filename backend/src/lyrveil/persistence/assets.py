"""SQL of the character assets: the `character_assets` rows and the primary avatar of the visual profile.

Every function works on a connection the caller owns and never commits. Deleted rows (`deleted_at` set) are invisible
to every read here.
"""

import json
import uuid
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.assets import StoredAsset

_COLUMNS = (
    "id, character_id, kind, source, file_path, thumb_path, media_type, width, height, byte_size, sha256, crop_json, "
    "is_primary, created_at, generated_image_id"
)


def _asset(row: RowMapping) -> StoredAsset:
    return StoredAsset(
        id=row["id"],
        character_id=row["character_id"],
        kind=row["kind"],
        source=row["source"],
        file_path=row["file_path"],
        thumb_path=row["thumb_path"],
        media_type=row["media_type"],
        width=row["width"],
        height=row["height"],
        byte_size=row["byte_size"],
        sha256=row["sha256"],
        crop=None if row["crop_json"] is None else json.loads(row["crop_json"]),
        is_primary=bool(row["is_primary"]),
        created_at=row["created_at"],
        generated_image_id=row["generated_image_id"],
    )


def insert(connection: Connection, asset: StoredAsset) -> None:
    """Inserts `asset` as a live row. The partial unique index refuses a second primary avatar of the character."""
    connection.execute(
        text(
            f"INSERT INTO character_assets ({_COLUMNS}) VALUES (:id, :character_id, :kind, :source, :file_path, "
            ":thumb_path, :media_type, :width, :height, :byte_size, :sha256, :crop_json, :is_primary, :created_at, "
            ":generated_image_id)"
        ),
        {
            "id": asset.id,
            "character_id": asset.character_id,
            "kind": asset.kind,
            "source": asset.source,
            "file_path": asset.file_path,
            "thumb_path": asset.thumb_path,
            "media_type": asset.media_type,
            "width": asset.width,
            "height": asset.height,
            "byte_size": asset.byte_size,
            "sha256": asset.sha256,
            "crop_json": None if asset.crop is None else json.dumps(asset.crop, separators=(",", ":")),
            "is_primary": asset.is_primary,
            "created_at": asset.created_at,
            "generated_image_id": asset.generated_image_id,
        },
    )


def link_generated(connection: Connection, image: RowMapping) -> bool:
    """Adds the asset of a generated image (a `generated_images` row whose purpose is a GENERATED_KINDS value), and
    returns whether it wrote a row. The asset shares the image's files and takes the purpose as its kind.

    Idempotent: an image that already has its asset writes nothing, so a persist that restarts adds no second one. The
    asset id derives from the image id. A generated avatar becomes primary only when the character has none.
    """
    exists = connection.execute(
        text("SELECT 1 FROM character_assets WHERE generated_image_id = :id"), {"id": image["id"]}
    ).first()
    if exists is not None:
        return False
    asset_id = str(uuid.uuid5(uuid.UUID(image["id"]), "asset"))
    insert(
        connection,
        StoredAsset(
            id=asset_id,
            character_id=image["character_id"],
            kind=image["purpose"],
            source="generated",
            file_path=image["file_path"],
            thumb_path=image["thumb_path"],
            media_type=image["media_type"],
            width=image["width"],
            height=image["height"],
            byte_size=image["byte_size"],
            sha256=image["sha256"],
            crop=None,
            is_primary=False,
            created_at=image["created_at"],
            generated_image_id=image["id"],
        ),
    )
    if image["purpose"] == "avatar" and not has_primary(connection, image["character_id"]):
        set_primary(connection, character_id=image["character_id"], asset_id=asset_id, now=image["created_at"])
    return True


def get(connection: Connection, asset_id: str) -> StoredAsset | None:
    """Returns the live asset with that id, or None."""
    row = (
        connection.execute(
            text(f"SELECT {_COLUMNS} FROM character_assets WHERE id = :id AND deleted_at IS NULL"), {"id": asset_id}
        )
        .mappings()
        .first()
    )
    return None if row is None else _asset(row)


def list_for(connection: Connection, character_id: str, kind: str | None) -> list[StoredAsset]:
    """Returns the live assets of the character, of `kind` when it is given, newest first."""
    sql = f"SELECT {_COLUMNS} FROM character_assets WHERE character_id = :id AND deleted_at IS NULL"
    params: dict[str, Any] = {"id": character_id}
    if kind is not None:
        sql += " AND kind = :kind"
        params["kind"] = kind
    sql += " ORDER BY created_at DESC, id DESC"
    return [_asset(row) for row in connection.execute(text(sql), params).mappings()]


def has_primary(connection: Connection, character_id: str) -> bool:
    """Returns whether the character has a primary avatar."""
    return (
        connection.execute(
            text(
                "SELECT 1 FROM character_assets WHERE character_id = :id AND is_primary = 1 AND kind = 'avatar' "
                "AND deleted_at IS NULL LIMIT 1"
            ),
            {"id": character_id},
        ).first()
        is not None
    )


def set_primary(connection: Connection, *, character_id: str, asset_id: str, now: str) -> None:
    """Makes the live avatar `asset_id` the only primary avatar of the character and points the visual profile at it.
    The old primary is cleared first, because the partial unique index checks every statement."""
    params = {"character_id": character_id, "asset_id": asset_id, "now": now}
    connection.execute(
        text("UPDATE character_assets SET is_primary = 0 WHERE character_id = :character_id AND is_primary = 1"),
        params,
    )
    connection.execute(text("UPDATE character_assets SET is_primary = 1 WHERE id = :asset_id"), params)
    connection.execute(
        text(
            "UPDATE character_visual_profiles SET primary_avatar_asset_id = :asset_id, updated_at = :now "
            "WHERE character_id = :character_id"
        ),
        params,
    )


def mark_deleted(connection: Connection, *, asset_id: str, now: str) -> None:
    """Sets `deleted_at` on a non-primary asset. The row stays; its files are the caller's to remove."""
    connection.execute(
        text("UPDATE character_assets SET deleted_at = :now WHERE id = :id AND is_primary = 0"),
        {"id": asset_id, "now": now},
    )


def file_paths(connection: Connection, character_id: str) -> list[str]:
    """Returns the file and thumbnail paths of every asset of the character, deleted ones included, for a purge."""
    rows = connection.execute(
        text("SELECT file_path, thumb_path FROM character_assets WHERE character_id = :id ORDER BY id"),
        {"id": character_id},
    )
    return [path for row in rows for path in row]
