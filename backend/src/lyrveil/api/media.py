"""Media routes of docs/api.md (Images): the files of character assets, served outside the JSON API under `/media`.

A route takes an identifier and looks the path up in the database; no part of the request path becomes a file path
(docs/security.md, File safety). The image routes arrive with the image pipeline (P05).
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from lyrveil.api.assets import Service
from lyrveil.services.assets import CharacterAssetService

router = APIRouter(prefix="/media", tags=["media"], include_in_schema=False)

# A file never changes once written: a crop or a new upload gets a new id, so the browser may keep it.
_CACHE_CONTROL = "private, max-age=31536000, immutable"


def _file(service: CharacterAssetService, asset_id: str, *, thumbnail: bool) -> FileResponse:
    path, media_type = service.media_file(asset_id, thumbnail=thumbnail)
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": _CACHE_CONTROL})


@router.get("/assets/{asset_id}")
def read_asset_file(asset_id: str, service: CharacterAssetService = Service) -> FileResponse:
    """Serves the file of a live asset with its media type. Answers 404 `NOT_FOUND` for an id that names no live
    asset."""
    return _file(service, asset_id, thumbnail=False)


@router.get("/assets/{asset_id}/thumbnail")
def read_asset_thumbnail(asset_id: str, service: CharacterAssetService = Service) -> FileResponse:
    """Serves the WebP thumbnail of a live asset, longest side at most 512 pixels. Answers 404 `NOT_FOUND` for an id
    that names no live asset."""
    return _file(service, asset_id, thumbnail=True)
