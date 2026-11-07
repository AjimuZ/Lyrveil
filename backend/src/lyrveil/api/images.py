"""Image routes of docs/api.md (Images): the metadata of a generated image. The files are served by api/media.py.

Delivered with P05.4b: `GET /images/{id}`. The gallery, the explicit request, regeneration and deletion routes come
with P05.5 and P05.6.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Engine

from lyrveil.media.store import MediaStore
from lyrveil.services.images import ImageService

router = APIRouter(tags=["images"], include_in_schema=False)


def get_image_service(request: Request) -> ImageService:
    """Returns the service on the application's engine and media store; startup always opens both first."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the image routes need a database")
    media: MediaStore = request.app.state.media
    return ImageService(engine, media)


ImageApiService = Depends(get_image_service)


@router.get("/images/{image_id}")
def read_image(image_id: str, service: ImageService = ImageApiService) -> dict[str, Any]:
    """Returns the metadata of a live image: its size, the request, the scene, the final prompts, the seed and the
    sampler values, the profile and the preset that produced the file, and when. The submitted workflow stays out.
    Answers 404 `NOT_FOUND` for an id that names no live image."""
    metadata = service.get(image_id)
    return {
        **metadata,
        "url": f"/media/images/{image_id}/original",
        "thumbnail_url": f"/media/images/{image_id}/thumbnail",
    }
