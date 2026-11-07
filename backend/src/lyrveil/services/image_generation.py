"""ImageGenerationService: the handler of `image_request`, which runs the `prompt`, `render` and `persist` stages of
one image (docs/image-generation.md, Job stages and Persistence).

`prompt` runs ImagePromptStage under the llm lease: the caption, the scene, the final prompt and the size. It also
chooses the seed, unless the request gave one, so a retried `render` reproduces the same request. `render` builds the
workflow of the quality preset, follows ComfyUI's progress under the image lease and stages the file at
`.staging/<job id>/render`. `persist` validates the staged file with Pillow, writes the thumbnail and the original,
then stores `generated_images`, links the variant and completes the message in one transaction. Each stage hands the
next one the whole payload, so a restart continues from the last finished stage.

The message stays `queued` while its job works, so the startup rule that interrupts `streaming` messages never touches
it. Persistence is idempotent: `generated_images.job_id` is unique and the file names derive from the job id, so a
`persist` that runs twice leaves one image. No prompt, scene, caption or request text is ever logged.
"""

import json
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Self

import anyio
from anyio import to_thread
from sqlalchemy import Connection, Engine, RowMapping

from lyrveil.catalog import ImagePreset, ImageProfile
from lyrveil.context.builder import ContextRequest, ContextSettings
from lyrveil.domain.assets import GENERATED_KINDS
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.errors import ContentPolicyBlockedError, DomainError
from lyrveil.domain.identifiers import to_utc_text
from lyrveil.domain.messages import Message, MessageStatus, VariantStatus, content_hash, message_document
from lyrveil.images.workflows import (
    ALTA_SCALE,
    MAX_SEED,
    TEMPLATES,
    Aspect,
    QualityPreset,
    RequestedAspect,
    WorkflowError,
    WorkflowSpec,
    build_workflow,
)
from lyrveil.jobs.handlers import (
    INTERNAL,
    JobDone,
    JobFailedError,
    JobOutcome,
    JobPreemptedError,
    NextStage,
    RunContext,
)
from lyrveil.jobs.queue import Job
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.media.images import prepare_upload
from lyrveil.media.store import MediaPathError, MediaStore
from lyrveil.persistence import assets as asset_repository
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import images as image_repository
from lyrveil.persistence import messages as repository
from lyrveil.persistence.images import NewImage
from lyrveil.ports.events import IMAGE_COMPLETED, IMAGE_PROGRESS, MESSAGE_COMPLETED, MESSAGE_FAILED, EventPublisher
from lyrveil.ports.image_runtime import (
    ImageOutOfMemoryError,
    ImageProgress,
    ImageRequest,
    ImageRuntime,
    ImageRuntimeError,
    ImageRuntimeUnavailableError,
    RenderFinished,
    RenderStalledError,
    RenderTimeoutError,
)
from lyrveil.ports.memory_retriever import MemoryRetriever
from lyrveil.ports.text_runtime import TextRuntime, TextRuntimeError
from lyrveil.services.chat_inference import ReplySettings, load_context_request
from lyrveil.services.image_prompt import ImagePromptRequest, ImagePromptStage
from lyrveil.services.memory_retrieval import FtsMemoryRetriever

STAGE_PROMPT = "prompt"
STAGE_RENDER = "render"
STAGE_PERSIST = "persist"
# docs/api.md (Errors): the job error of a request the card cannot satisfy, such as an outfit it does not offer.
VALIDATION_ERROR = "VALIDATION_ERROR"
# The media type of the runtime's SaveImage node, which `persist` validates the staged file as.
_MEDIA_TYPE = "image/png"
_RETRYABLE_RUNTIME_ERRORS = (
    ImageOutOfMemoryError,
    ImageRuntimeUnavailableError,
    RenderStalledError,
    RenderTimeoutError,
)
Phase = Literal["prompt", "loading", "sampling", "refining", "saving"]

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def random_seed() -> int:
    """Returns a seed drawn from the operating system's randomness, in the range ComfyUI accepts."""
    return secrets.randbelow(MAX_SEED + 1)


@dataclass(frozen=True)
class ImageSettings:
    """What an image takes besides its request: the image profile's checkpoint, its prompt preset, the default quality
    and the sampler values of docs/image-generation.md (Default parameters).

    P05 has no settings store yet, so the defaults are the documented ones of `illustrious-xl-v2.0`.
    `checkpoint_file` always comes from the catalog or a registered custom profile, never from user text.
    """

    profile_id: str = "illustrious-xl-v2.0"
    checkpoint_file: str = "Illustrious-XL-v2.0.safetensors"
    checkpoint_sha256: str | None = None
    preset: ImagePreset = "illustrious"
    quality: QualityPreset = "alta"
    steps: int = 28
    cfg: float = 5.5
    sampler: str = "euler_ancestral"
    scheduler: str = "normal"
    clip_skip: int = 2
    refine_steps: int = 14
    refine_denoise: float = 0.45

    @classmethod
    def for_profile(cls, profile: ImageProfile, quality: QualityPreset = "alta") -> Self:
        """Returns the settings of a catalog image profile, with the documented sampler values."""
        return cls(
            profile_id=profile.id,
            checkpoint_file=profile.file,
            checkpoint_sha256=profile.sha256,
            preset=profile.defaults,
            quality=quality,
        )


@dataclass(frozen=True)
class _Target:
    """What the `prompt` stage reads once: the card, and the reply context of the image's message, if it has one."""

    card: CharacterCard
    chat: ContextRequest | None


def _sampling(payload: Mapping[str, Any], settings: ImageSettings) -> tuple[int, float]:
    """Returns the step count and CFG of a job: the request's own when the form gave them, else the settings'."""
    steps = payload.get("steps")
    cfg = payload.get("cfg")
    return (settings.steps if steps is None else steps), (settings.cfg if cfg is None else cfg)


def job_error(error: Exception) -> Exception:
    """Returns the exception the scheduler should record for `error`.

    A JobFailedError, a TextRuntimeError (which the scheduler retries with its code) and anything unknown pass through.
    A blocked scene is `CONTENT_POLICY_BLOCKED` and never retried; a request the card cannot satisfy is
    `VALIDATION_ERROR`; a runtime that is out of memory, stopped answering or stalled is retried with the code of the
    port's error; a node that failed, a file Pillow refuses and a defect of the templates are not. A disk error is
    retried. No detail carries private text.
    """
    if isinstance(error, JobFailedError | TextRuntimeError):
        return error
    if isinstance(error, ContentPolicyBlockedError):
        return JobFailedError("CONTENT_POLICY_BLOCKED", retryable=False)
    if isinstance(error, ImageRuntimeError):
        return JobFailedError(error.code, retryable=isinstance(error, _RETRYABLE_RUNTIME_ERRORS))
    if isinstance(error, WorkflowError | DomainError | MediaPathError):
        return JobFailedError(INTERNAL, retryable=False)
    if isinstance(error, ValueError):
        return JobFailedError(VALIDATION_ERROR, "La richiesta non è valida per questo personaggio.", retryable=False)
    if isinstance(error, OSError):
        return JobFailedError(INTERNAL, retryable=True)
    return error


class ImageGenerationService:
    """Runs one stage of an `image_request` job per call, as the JobHandler of that kind (lyrveil.jobs.handlers).

    `text_runtime` serves the `prompt` stage and `image_runtime` the `render` stage; each must be used only under the
    stage's lease, which the scheduler holds. `media` keeps the staged and final files, `events` receives each event
    after the change it announces was committed. `settings` and `replies` give the image and reply defaults,
    `retriever` finds the caption's memories (by default an FtsMemoryRetriever on `engine`), and `seed_source` and
    `clock` are replaced in tests.
    """

    def __init__(
        self,
        engine: Engine,
        text_runtime: TextRuntime,
        image_runtime: ImageRuntime,
        media: MediaStore,
        events: EventPublisher,
        settings: ImageSettings | None = None,
        replies: ReplySettings | None = None,
        *,
        context: ContextSettings | None = None,
        retriever: MemoryRetriever | None = None,
        seed_source: Callable[[], int] = random_seed,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._images = image_runtime
        self._media = media
        self._events = events
        self._settings = settings or ImageSettings()
        self._replies = replies or ReplySettings()
        self._stage = ImagePromptStage(
            text_runtime, retriever or FtsMemoryRetriever(engine), self._replies, context=context
        )
        self._seed_source = seed_source
        self._clock = clock

    async def __call__(self, job: Job, context: RunContext) -> JobOutcome:
        """Runs the stage the job waits in and returns its outcome: NextStage with the whole payload for `prompt` and
        `render`, JobDone with the image and message ids for `persist`, or `skipped` when the message is gone.

        On a failure the message and its variant become `failed` and `message.failed` is published, unless the
        scheduler will retry the stage; then the failure is raised as job_error gives it. A preempted or cancelled
        run (a lost lease, a shutdown) changes nothing.
        """
        try:
            if job.stage == STAGE_PROMPT:
                return await self._prompt(job, context)
            if job.stage == STAGE_RENDER:
                return await self._render(job)
            if job.stage == STAGE_PERSIST:
                return await self._persist(job)
            raise JobFailedError(INTERNAL, retryable=False)
        except JobPreemptedError:
            raise
        except Exception as error:
            converted = job_error(error)
            await self._failed(job, converted)
            if converted is error:
                raise
            raise converted from error

    async def abandon(self, job: Job, error: JobFailedError) -> None:
        """Ends the image of `job` with `error` before its stage ran (AbandoningHandler): the message and its variant
        become `failed`, any staged file is removed, and `message.failed` carries the error's code."""
        await self._failed(job, error)

    async def _prompt(self, job: Job, context: RunContext) -> JobOutcome:
        target = await to_thread.run_sync(self._load_target, job)
        if target is None:
            return JobDone({"skipped": True})
        self._progress(job, "prompt")
        payload = job.payload
        quality: QualityPreset = payload.get("quality") or self._settings.quality
        aspect: RequestedAspect = payload.get("aspect") or "auto"
        request = ImagePromptRequest(
            card=target.card,
            request=payload["request"],
            preset=self._settings.preset,
            quality=quality,
            aspect=aspect,
            outfit=payload.get("outfit"),
        )
        prepared = await self._stage.run(request, target.chat, context.genkey)
        seed = payload.get("seed")
        saved: dict[str, Any] = {
            **payload,
            "quality": quality,
            "seed": self._seed_source() if seed is None else seed,
            "caption": prepared.caption.text,
            "caption_failure": prepared.caption.failure,
            "caption_checks": dict(prepared.caption.checks),
            "scene": prepared.scene.scene.model_dump(mode="json"),
            "scene_fallback": prepared.scene.fallback,
            "positive": prepared.prompt.positive,
            "negative": prepared.prompt.negative,
            "outfit_key": prepared.prompt.outfit_key,
            "aspect": prepared.aspect,
            "width": prepared.width,
            "height": prepared.height,
        }
        logger.info(
            "image_prompt_ready",
            job_id=job.id,
            caption_present=int(prepared.caption.text is not None),
            scene_fallback=int(prepared.scene.fallback),
        )
        return NextStage(STAGE_RENDER, saved)

    def _load_target(self, job: Job) -> _Target | None:
        """Returns the card and reply context of the job's message, or None when the message is gone. Blocks: run it in
        a thread."""

        def load(connection: Connection) -> _Target | None:
            message = None if job.message_id is None else repository.get_message(connection, job.message_id)
            if job.message_id is not None and (message is None or message.deleted_at is not None):
                return None
            if message is not None:
                chat = load_context_request(
                    connection, message.conversation_id, message.seq, user=self._replies.user, now=self._clock()
                )
                if chat is not None:
                    return _Target(chat.card, chat)
            assert job.character_id is not None, "an image job names its character"
            stored = character_repository.get_card(connection, job.character_id)
            assert stored is not None, "a character with a job cannot be removed"
            return _Target(stored.card, None)

        return read(self._engine, load)

    async def _render(self, job: Job) -> JobOutcome:
        payload = job.payload
        quality: QualityPreset = payload["quality"]
        aspect: Aspect = payload["aspect"]
        settings = self._settings
        steps, cfg = _sampling(payload, settings)
        # The second attempt follows an out-of-memory failure or a stall, so it decodes in tiles (docs/backend.md,
        # Retry policy). The preset used is the one stored with the image.
        tiled = job.attempt > 1
        workflow = build_workflow(
            WorkflowSpec(
                preset=quality,
                aspect=aspect,
                ckpt_name=settings.checkpoint_file,
                positive=payload["positive"],
                negative=payload["negative"],
                seed=payload["seed"],
                tiled=tiled,
                steps=steps,
                cfg=cfg,
                sampler=settings.sampler,
                scheduler=settings.scheduler,
                clip_skip=settings.clip_skip,
                refine_steps=settings.refine_steps,
                refine_denoise=settings.refine_denoise,
            )
        )
        version = (await self._images.info()).version
        prompt_id = str(uuid.uuid5(uuid.UUID(job.id), f"render-{job.attempt}"))
        finished: RenderFinished | None = None
        async for event in self._images.render(ImageRequest(workflow=workflow, prompt_id=prompt_id)):
            if isinstance(event, ImageProgress):
                self._progress(job, event.phase, event.value, event.max)
            else:
                finished = event
        if finished is None or finished.status != "success":
            # Someone else interrupted the render, or the runtime closed without a result: the stage runs again.
            raise JobFailedError("RUNTIME_UNAVAILABLE", retryable=True)
        data = await self._images.download(finished.images[0])
        await to_thread.run_sync(self._media.stage_job_file, job.id, data)
        return NextStage(
            STAGE_PERSIST,
            {
                **payload,
                "workflow": workflow,
                "workflow_id": TEMPLATES[(quality, tiled)],
                "comfyui_version": version,
            },
        )

    async def _persist(self, job: Job) -> JobOutcome:
        self._progress(job, "saving")
        image, message = await to_thread.run_sync(self._store, job)
        await to_thread.run_sync(self._media.discard_staged, job.id)
        self._events.publish(IMAGE_COMPLETED, {"image": image, "message_id": None if message is None else message.id})
        if message is not None:
            self._events.publish(MESSAGE_COMPLETED, {"message": message_document(message)})
        logger.info("image_persisted", job_id=job.id, image_id=image["id"])
        return JobDone({"image_id": image["id"], "message_id": job.message_id})

    def _store(self, job: Job) -> tuple[dict[str, Any], Message | None]:
        """Persists the staged file of `job` once and returns the image's metadata and its completed message.

        Writes nothing again when `generated_images` already holds the job's image (a restart after the commit); it
        then only completes the message, which is idempotent. Blocks: run it in a thread. Raises JobFailedError when
        the staged file is missing and nothing was persisted, and the errors of prepare_upload for a file that is not
        a valid image.
        """
        engine = self._engine
        already = read(engine, lambda connection: image_repository.find_by_job(connection, job.id)) is not None
        new = None if already else self._write_files(job)
        payload = job.payload
        variant_id: str | None = payload.get("variant_id")

        def commit(connection: Connection) -> tuple[RowMapping, Message | None]:
            if new is not None:
                image_repository.insert(connection, new)
            found = image_repository.find_by_job(connection, job.id)
            assert found is not None
            if found["purpose"] in GENERATED_KINDS:
                asset_repository.link_generated(connection, found)
            if job.message_id is None or variant_id is None:
                return found, None
            caption: str = payload.get("caption") or ""
            checks = payload.get("caption_checks") or None
            image_repository.link_variant(connection, variant_id, found["id"])
            repository.update_variant(
                connection,
                variant_id,
                content=caption,
                content_hash=content_hash(caption),
                status=VariantStatus.COMPLETE.value,
                checks_json=None if checks is None else json.dumps(checks, ensure_ascii=False, separators=(",", ":")),
            )
            repository.update_message(
                connection,
                job.message_id,
                kind="mixed" if caption else "image",
                content=caption,
                status=MessageStatus.COMPLETE.value,
                active_variant_id=variant_id,
            )
            return found, repository.get_message(connection, job.message_id)

        found, message = write(engine, commit)
        return image_repository.document(found), message

    def _write_files(self, job: Job) -> NewImage:
        """Validates the staged file, writes the thumbnail and then the original, and returns the row to insert.

        The identifier and both paths derive from the job id and the clock's month, so a restart overwrites the same
        files. Raises JobFailedError when nothing is staged.
        """
        data = self._media.read_staged(job.id)
        if data is None:
            raise JobFailedError(INTERNAL, "Il file dell'immagine non è più disponibile.", retryable=False)
        encoded = prepare_upload(data, _MEDIA_TYPE)
        payload = job.payload
        settings = self._settings
        quality: QualityPreset = payload["quality"]
        now = self._clock()
        image_id = str(uuid.uuid5(uuid.UUID(job.id), "image"))
        folder = f"{now:%Y}/{now:%m}"
        file_path = f"images/{folder}/{image_id}.{encoded.format.extension}"
        thumb_path = f"thumbs/{folder}/{image_id}.webp"
        self._media.save(thumb_path, encoded.thumbnail)
        self._media.save(file_path, encoded.data)
        alta = quality == "alta"
        steps, cfg = _sampling(payload, settings)
        assert job.character_id is not None, "an image job names its character"
        return NewImage(
            id=image_id,
            job_id=job.id,
            message_id=job.message_id,
            conversation_id=job.conversation_id,
            character_id=job.character_id,
            purpose=payload.get("purpose") or "chat",
            file_path=file_path,
            thumb_path=thumb_path,
            media_type=encoded.format.media_type,
            width=encoded.width,
            height=encoded.height,
            byte_size=len(encoded.data),
            sha256=encoded.sha256,
            user_request=payload["request"],
            scene=payload["scene"],
            aspect=payload["aspect"],
            outfit_key=payload["outfit_key"],
            prompt_positive=payload["positive"],
            prompt_negative=payload["negative"],
            seed=payload["seed"],
            steps=steps,
            cfg=cfg,
            sampler=settings.sampler,
            scheduler=settings.scheduler,
            clip_skip=settings.clip_skip,
            checkpoint_file=settings.checkpoint_file,
            checkpoint_sha256=settings.checkpoint_sha256,
            model_version=None,
            profile_id=settings.profile_id,
            quality_preset=quality,
            hires_denoise=settings.refine_denoise if alta else None,
            hires_scale=ALTA_SCALE if alta else None,
            hires_steps=settings.refine_steps if alta else None,
            workflow_id=payload["workflow_id"],
            workflow=payload["workflow"],
            comfyui_version=payload.get("comfyui_version"),
            created_at=to_utc_text(now),
        )

    async def _failed(self, job: Job, error: Exception) -> None:
        """Marks the message and its variant `failed` and announces it, unless the scheduler will run the stage again.

        A retry is expected for a retryable error with attempts left. The staged file is removed with the last
        attempt. A failure while recording is logged and never replaces the original error.
        """
        retryable = isinstance(error, TextRuntimeError) or (isinstance(error, JobFailedError) and error.retryable)
        if retryable and job.attempt < job.max_attempts:
            return
        code = error.code if isinstance(error, JobFailedError | TextRuntimeError) else INTERNAL
        try:
            with anyio.CancelScope(shield=True):
                await to_thread.run_sync(self._mark_failed, job)
            if job.message_id is not None:
                self._events.publish(MESSAGE_FAILED, {"message_id": job.message_id, "error_code": code})
        except Exception:
            logger.error("image_failure_not_recorded", job_id=job.id, exc_info=True)
        logger.info("image_failed", job_id=job.id, error_code=code)

    def _mark_failed(self, job: Job) -> None:
        """Removes the staged file and sets the message and its variant `failed`. Blocks: run it in a thread."""
        self._media.discard_staged(job.id)
        message_id = job.message_id
        if message_id is None:
            return
        variant_id: str | None = job.payload.get("variant_id")

        def mark(connection: Connection) -> None:
            if variant_id is not None:
                repository.update_variant(connection, variant_id, status=VariantStatus.FAILED.value)
            repository.update_message(connection, message_id, status=MessageStatus.FAILED.value)

        write(self._engine, mark)

    def _progress(self, job: Job, phase: Phase, value: int | None = None, maximum: int | None = None) -> None:
        self._events.publish(
            IMAGE_PROGRESS,
            {"job_id": job.id, "message_id": job.message_id, "phase": phase, "value": value, "max": maximum},
        )
