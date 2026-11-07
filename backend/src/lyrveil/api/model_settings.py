"""Model settings routes of docs/api.md (Jobs, scheduling, notifications, settings, data), over ModelProfiles.

`GET /settings/models`, `PATCH /settings/models` (the active text profile), `POST /settings/models/apply` (**Applica
ora**), `POST /settings/models/custom` and `DELETE /settings/models/custom/{id}`. The image profile, the quality preset
and the runtime values of `PATCH` are planned. Not in the committed OpenAPI document yet, for the reason given in
lyrveil.api.conversations.
"""

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from lyrveil.api.jobs import JobOut, wake_worker
from lyrveil.catalog import ID_PATTERN, TextTemplate
from lyrveil.jobs.queue import Job
from lyrveil.jobs.scheduler import read
from lyrveil.persistence import messages as message_repository
from lyrveil.services.model_profiles import ModelProfiles

router = APIRouter(prefix="/settings/models", tags=["settings"], include_in_schema=False)


class ModelSettingsPatch(BaseModel):
    """The changes of PATCH /settings/models. Only the text profile can change so far; an unknown key is refused."""

    model_config = ConfigDict(extra="forbid")

    llm_profile_id: str = Field(pattern=ID_PATTERN)


class CustomRegistration(BaseModel):
    """A file of a model folder to register, by name. `template` is required for a `text` file and refused for an
    `image` checkpoint, which is always SDXL with epsilon prediction."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "image"]
    file: str = Field(min_length=1, max_length=160)
    template: TextTemplate | None = None


def _profiles(request: Request) -> ModelProfiles:
    profiles: ModelProfiles | None = request.app.state.model_profiles
    if profiles is None:
        raise RuntimeError("the model settings routes need ModelProfiles")
    return profiles


def _queued(request: Request, job: Job) -> JobOut:
    wake_worker(request)
    position = read(request.app.state.engine, lambda connection: message_repository.queue_position(connection, job.id))
    return JobOut(id=job.id, kind=job.kind.value, status=job.status.value, queue_position=position)


@router.get("")
def read_model_settings(request: Request) -> dict[str, Any]:
    """Returns the tier, the active profiles, every catalog and custom profile with its local state (`verified`,
    `size_mismatch`, `missing`), the unregistered files of the two model folders and the launch values."""
    return _profiles(request).overview()


@router.patch("")
def change_model_settings(body: ModelSettingsPatch, request: Request) -> dict[str, Any]:
    """Makes `llm_profile_id` the active text profile and writes its launch file; the runtime loads it at its next
    start, or at once through /settings/models/apply. Answers the new settings, or 409 `MODEL_PROFILE_UNAVAILABLE`,
    changing nothing, for a profile that does not exist, needs a larger tier, or whose file is missing."""
    profiles = _profiles(request)
    profiles.select_text(body.llm_profile_id)
    return profiles.overview()


@router.post("/apply", status_code=202, response_model=JobOut)
def apply_model_settings(request: Request) -> JobOut:
    """Queues the reload of the text model ahead of replies and answers the job. Answers 409
    `MODEL_PROFILE_UNAVAILABLE`, queuing nothing, when the launch file's model file is missing or of another size."""
    return _queued(request, _profiles(request).request_reload())


@router.post("/custom", status_code=202, response_model=JobOut)
def register_custom_model(body: CustomRegistration, request: Request) -> JobOut:
    """Queues the hash job that registers a file already in its model folder, and answers the job. Answers 422
    `VALIDATION_ERROR` for a refused name, extension, link or template, 404 for a file not in the folder, and 409
    `CONFLICT` for a catalog file or one already registered."""
    return _queued(request, _profiles(request).register_custom(body.kind, body.file, body.template))


@router.delete("/custom/{profile_id}", status_code=204)
def remove_custom_model(profile_id: str, request: Request) -> Response:
    """Removes the registration, never the file. Answers 404 for an unknown profile and 409 `CONFLICT` while it is
    the active text profile."""
    _profiles(request).unregister_custom(profile_id)
    return Response(status_code=204)
