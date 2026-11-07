"""Asset routes of docs/api.md (Assets): list, upload, generate, crop, primary avatar and delete. Generation (character
mode) only queues the job; the pipeline links its result as an asset. Not in the committed OpenAPI document yet, for
the reason given in lyrveil.api.conversations.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from lyrveil.api.messages import JobOut, _job, _wake_worker
from lyrveil.domain.assets import StoredAsset
from lyrveil.media.images import MAX_UPLOAD_BYTES
from lyrveil.media.store import MediaStore
from lyrveil.services.assets import CharacterAssetService
from lyrveil.services.images import MAX_REQUEST_CHARS, ImageService

router = APIRouter(prefix="/characters", tags=["assets"], include_in_schema=False)


class AssetOut(BaseModel):
    """An asset as the profile shows it. `url` and `thumbnail_url` are the media routes that serve its files."""

    id: str
    character_id: str
    kind: str
    source: str
    media_type: str
    width: int
    height: int
    byte_size: int
    sha256: str
    crop: dict[str, Any] | None
    is_primary: bool
    created_at: str
    url: str
    thumbnail_url: str


class AssetList(BaseModel):
    items: list[AssetOut]


class CropBody(BaseModel):
    """A rectangle in pixels of the source image. Whether it fits inside the image is checked by the service."""

    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class GenerateBody(BaseModel):
    """Character mode of docs/image-generation.md: `purpose` is `avatar`, `profile`, `scene` or `variant`, and `scene`
    describes the picture (it may be blank except for `scene`). Aspect, outfit, quality and seed take the values of a
    chat image request, which the service checks."""

    model_config = ConfigDict(extra="forbid")

    purpose: str = "avatar"
    scene: str = Field(default="", max_length=MAX_REQUEST_CHARS)
    aspect: str = "square"
    outfit: str | None = None
    quality: str | None = None
    seed: int | None = None


class GenerateResult(BaseModel):
    job: JobOut


def asset_out(asset: StoredAsset) -> AssetOut:
    return AssetOut(
        id=asset.id,
        character_id=asset.character_id,
        kind=asset.kind,
        source=asset.source,
        media_type=asset.media_type,
        width=asset.width,
        height=asset.height,
        byte_size=asset.byte_size,
        sha256=asset.sha256,
        crop=asset.crop,
        is_primary=asset.is_primary,
        created_at=asset.created_at,
        url=f"/media/assets/{asset.id}",
        thumbnail_url=f"/media/assets/{asset.id}/thumbnail",
    )


def get_service(request: Request) -> CharacterAssetService:
    """Returns the service on the application's engine and media store; startup always opens both first."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the asset routes need a database")
    media: MediaStore = request.app.state.media
    return CharacterAssetService(engine, media)


Service = Depends(get_service)


def get_image_service(request: Request) -> ImageService:
    """Returns the image service on the application's engine and media store."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the asset routes need a database")
    return ImageService(engine, request.app.state.media)


ImageApiService = Depends(get_image_service)


@router.get("/{character_id}/assets", response_model=AssetList)
def list_assets(character_id: str, kind: str | None = None, service: CharacterAssetService = Service) -> AssetList:
    """Lists the live assets of the character, newest first, of `kind` when given. Answers 404 `NOT_FOUND` for an
    unknown character and 422 `VALIDATION_ERROR` for an unknown kind."""
    return AssetList(items=[asset_out(item) for item in service.list_assets(character_id, kind)])


@router.post("/{character_id}/assets", response_model=AssetOut, status_code=201)
async def upload_asset(
    character_id: str,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "avatar",
    service: CharacterAssetService = Service,
) -> AssetOut:
    """Stores a multipart upload (`file`, and `kind`: `avatar` by default, `profile` or `reference`) re-encoded without
    metadata, with a WebP thumbnail, and answers it with `201`. The first avatar becomes the primary one. Answers 413
    `PAYLOAD_TOO_LARGE` over 15 MB or 8192 x 8192 pixels, 415 `UNSUPPORTED_MEDIA` for a file that is not PNG, JPEG or
    WebP or whose declared type disagrees with its bytes, 404 `NOT_FOUND` for an unknown character and 422
    `VALIDATION_ERROR` for another kind."""
    # One byte over the limit is enough to refuse, so a larger file is never read whole into memory.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    # Decoding up to 8192 x 8192 pixels takes a while; it runs in a worker thread so the event loop keeps serving.
    stored = await run_in_threadpool(service.upload, character_id, kind, data, file.content_type)
    return asset_out(stored)


@router.post("/{character_id}/assets/generate", response_model=GenerateResult, status_code=202)
def generate_asset(
    character_id: str, body: GenerateBody, request: Request, service: ImageService = ImageApiService
) -> GenerateResult:
    """Queues an `image_request` job that draws the character outside any chat and answers `202` with the job. When it
    ends, the image joins the character's assets as `purpose` and appears in the gallery; a generated avatar becomes
    the primary one only if the character has none. Answers 404 `NOT_FOUND` for an unknown character, 409 `CONFLICT`
    for a full queue and 422 `VALIDATION_ERROR` for an unknown purpose, a blank scene of the `scene` purpose, an
    unknown aspect or quality, a seed out of range or an outfit the character does not have."""
    job = service.request_asset(
        character_id,
        body.purpose,
        body.scene,
        aspect=body.aspect,
        outfit=body.outfit,
        quality=body.quality,
        seed=body.seed,
    )
    _wake_worker(request)
    return GenerateResult(job=_job(job))


@router.post("/{character_id}/assets/{asset_id}/crop", response_model=AssetOut, status_code=201)
def crop_asset(character_id: str, asset_id: str, body: CropBody, service: CharacterAssetService = Service) -> AssetOut:
    """Saves the rectangle of the asset as a new 512 x 512 asset of the same kind and answers it with `201`; the
    original stays. A rectangle that is not square is centred and trimmed to a square. Answers 404 `NOT_FOUND` for an
    unknown character or asset and 422 `VALIDATION_ERROR` for a rectangle that leaves the image."""
    return asset_out(service.crop(character_id, asset_id, x=body.x, y=body.y, width=body.width, height=body.height))


@router.post("/{character_id}/assets/{asset_id}/primary", response_model=AssetOut)
def set_primary_asset(character_id: str, asset_id: str, service: CharacterAssetService = Service) -> AssetOut:
    """Makes the avatar the character's only primary avatar and answers it. Answers 404 `NOT_FOUND` for an unknown
    character or asset and 422 `VALIDATION_ERROR` for an asset that is not an avatar."""
    return asset_out(service.set_primary(character_id, asset_id))


@router.delete("/{character_id}/assets/{asset_id}", status_code=204)
def delete_asset(character_id: str, asset_id: str, service: CharacterAssetService = Service) -> Response:
    """Deletes the asset and removes its files, answering 204. Answers 404 `NOT_FOUND` for an unknown character or
    asset and 409 `CONFLICT` for the primary avatar until another one is chosen."""
    service.delete(character_id, asset_id)
    return Response(status_code=204)
