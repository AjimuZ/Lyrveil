"""Image routes of docs/api.md (Images): the explicit request, the answers to a contextual proposal and the metadata
of a generated image. The files are served by api/media.py.

Delivered with P05.4b: `GET /images/{id}`. Delivered with P05.5a: `POST /conversations/{id}/images` and `POST
/image-proposals/{id}/accept` and `/reject`. The gallery, regeneration and deletion routes come with P05.6.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from lyrveil.api.messages import SendResult, _job, _message, _wake_worker
from lyrveil.media.store import MediaStore
from lyrveil.services.image_proposals import ImageProposalService
from lyrveil.services.images import MAX_CFG, MAX_REQUEST_CHARS, MAX_STEPS, MIN_CFG, MIN_STEPS, ImageService

router = APIRouter(tags=["images"], include_in_schema=False)


class ImageRequestBody(BaseModel):
    """The explicit request of docs/api.md (Images). Aspect, quality and the outfit are checked by the service, which
    answers 422 `VALIDATION_ERROR`; the ranges of `seed`, `steps` and `cfg` too."""

    model_config = ConfigDict(extra="forbid")

    request: str = Field(max_length=MAX_REQUEST_CHARS)
    aspect: str = "auto"
    outfit: str | None = None
    quality: str | None = None
    seed: int | None = None
    steps: int | None = Field(default=None, ge=MIN_STEPS, le=MAX_STEPS)
    cfg: float | None = Field(default=None, ge=MIN_CFG, le=MAX_CFG)


def get_image_service(request: Request) -> ImageService:
    """Returns the service on the application's engine and media store; startup always opens both first."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the image routes need a database")
    media: MediaStore = request.app.state.media
    return ImageService(engine, media)


ImageApiService = Depends(get_image_service)


def get_proposal_service(request: Request) -> ImageProposalService:
    """Returns the proposal service on the application's engine, media store and event hub."""
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise RuntimeError("the image routes need a database")
    images = ImageService(engine, request.app.state.media)
    return ImageProposalService(engine, images, request.app.state.events)


ProposalService = Depends(get_proposal_service)


@router.post("/conversations/{conversation_id}/images", response_model=SendResult, status_code=202)
def request_image(
    conversation_id: str, body: ImageRequestBody, request: Request, service: ImageService = ImageApiService
) -> SendResult:
    """Saves the request as the user's message and queues the `image_request` job that answers it, in one
    transaction. 404 for a missing or deleted chat, 409 for an archived chat or a full queue, and 422 for a blank
    request, an unknown aspect or quality, a value out of range or an outfit the character does not have."""
    sent = service.request(
        conversation_id,
        body.request,
        aspect=body.aspect,
        outfit=body.outfit,
        quality=body.quality,
        seed=body.seed,
        steps=body.steps,
        cfg=body.cfg,
    )
    _wake_worker(request)
    return SendResult(message=_message(sent.message), reply=_message(sent.reply), job=_job(sent.job))


@router.post("/image-proposals/{proposal_id}/accept", response_model=SendResult, status_code=202)
def accept_proposal(proposal_id: str, request: Request, service: ImageProposalService = ProposalService) -> SendResult:
    """Answers yes to a contextual proposal: queues its image. `message` and `reply` are the image placeholder. 404
    for an unknown proposal, 409 for one already answered or expired, an archived chat or a full queue."""
    sent = service.accept(proposal_id)
    _wake_worker(request)
    return SendResult(message=_message(sent.message), reply=_message(sent.reply), job=_job(sent.job))


@router.post("/image-proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, service: ImageProposalService = ProposalService) -> dict[str, Any]:
    """Answers no to a contextual proposal and returns it. 404 for an unknown proposal, 409 for one already answered
    or expired."""
    return service.reject(proposal_id)


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
