"""Character asset values that hold without a database (docs/data-model.md, `character_assets`)."""

from dataclasses import dataclass
from typing import Any

ASSET_KINDS = frozenset({"avatar", "profile", "scene", "variant", "reference"})
# The kinds a user may upload; scenes and variants come from image generation.
UPLOAD_KINDS = frozenset({"avatar", "profile", "reference"})
# The kinds image generation produces in character mode (docs/image-generation.md, Character mode). They are also the
# `generated_images.purpose` values that join the assets of a character.
GENERATED_KINDS = frozenset({"avatar", "profile", "scene", "variant"})


@dataclass(frozen=True)
class StoredAsset:
    """A live asset of a character. `file_path` and `thumb_path` are relative to the media root and built from
    identifiers only. `crop` is None for an upload, and for a crop holds `source_asset_id` with the rectangle `x`, `y`,
    `width`, `height` it took from that asset. `is_primary` is true for at most one avatar per character.

    `generated_image_id` names the `generated_images` row of a generated asset, which shares its files: the image owns
    them, so deleting the asset leaves them in place. It is None for any other asset.
    """

    id: str
    character_id: str
    kind: str
    source: str
    file_path: str
    thumb_path: str
    media_type: str
    width: int
    height: int
    byte_size: int
    sha256: str
    crop: dict[str, Any] | None
    is_primary: bool
    created_at: str
    generated_image_id: str | None = None
