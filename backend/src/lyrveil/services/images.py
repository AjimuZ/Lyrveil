"""ImageService: queues `image_request` jobs for a chat, regenerates stored images as variants, and reads image
metadata and files (docs/image-generation.md, Regeneration and Persistence).

A request saves first (docs/architecture.md, invariant 5): the user's request as a message, the image message
placeholder with its `streaming` variant, and the job are committed in one transaction, so a refused request leaves
nothing behind. The placeholder stays `queued` until ImageGenerationService completes or fails it.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

from sqlalchemy import Connection, Engine, text

from lyrveil.domain.assets import GENERATED_KINDS
from lyrveil.domain.conversations import Conversation, ConversationStatus
from lyrveil.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.messages import (
    PENDING,
    Author,
    JobRef,
    Message,
    MessageStatus,
    Regeneration,
    SentMessage,
    VariantSource,
    VariantStatus,
    content_hash,
)
from lyrveil.images.workflows import MAX_SEED, RequestedAspect, final_size
from lyrveil.jobs.policy import JobKind
from lyrveil.jobs.queue import JobQueue, QueueFullError
from lyrveil.jobs.scheduler import read, write
from lyrveil.logs import get_logger
from lyrveil.media.store import MediaPathError, MediaStore
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import conversations as conversation_repository
from lyrveil.persistence import images as image_repository
from lyrveil.persistence import messages as repository
from lyrveil.prompts.image_scene import outfit_choices
from lyrveil.services.image_generation import STAGE_RENDER, random_seed

MAX_REQUEST_CHARS = 1000
# docs/configuration.md (`images.steps`, `images.cfg`): the ranges a request may override.
MIN_STEPS, MAX_STEPS = 10, 50
MIN_CFG, MAX_CFG = 1.0, 12.0
# What each purpose draws when the form gives no scene; `scene` has none, so it needs a description.
DEFAULT_SCENES = {
    "avatar": "ritratto del personaggio, primo piano del viso, sguardo verso chi guarda, sfondo semplice",
    "profile": "il personaggio a mezzo busto, in posa, con uno sfondo che richiama il suo mondo",
    "variant": "il personaggio in una posa diversa, con la stessa espressione di sempre",
}
_ASPECTS = frozenset(get_args(RequestedAspect))
_QUALITIES = frozenset({"alta", "rapida"})
_NO_CHARACTER = "Il personaggio non esiste."
_NO_OUTFIT = "Il personaggio non ha questo abito."
_ALREADY_SENT = "Questo messaggio è già stato inviato come testo."
_NO_CHAT = "Conversazione non trovata."
_NO_IMAGE = "Immagine non trovata."
_QUEUE_FULL = "Troppe richieste in coda: riprova tra poco."
_BUSY = "Il messaggio è ancora in elaborazione."
_ONLY_LAST = "Solo l'ultimo messaggio della conversazione può essere rigenerato."

logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ImageService:
    """Queues image jobs on `queue` and reads the images of `engine`, whose files live in `media`.

    `clock` returns the aware current time and `seed_source` a new seed; tests pass fakes.
    """

    def __init__(
        self,
        engine: Engine,
        media: MediaStore,
        queue: JobQueue | None = None,
        clock: Callable[[], datetime] = _utc_now,
        seed_source: Callable[[], int] = random_seed,
    ) -> None:
        self._engine = engine
        self._media = media
        self._clock = clock
        self._queue = queue or JobQueue(clock=clock)
        self._seed_source = seed_source

    def request(
        self,
        conversation_id: str,
        request: str,
        *,
        aspect: str = "auto",
        outfit: str | None = None,
        quality: str | None = None,
        seed: int | None = None,
        steps: int | None = None,
        cfg: float | None = None,
        client_message_id: str | None = None,
    ) -> SentMessage:
        """Saves `request` as the user's message, adds the image message that will show the result and queues its
        `image_request` job, all in one transaction. Returns the user's message, the image placeholder (status
        `queued`, empty text, one `streaming` variant) and the job.

        `aspect` is `auto` or a fixed aspect, `outfit` is None, `usual`, `scene` or a key of the character's wardrobe,
        `quality` is `alta`, `rapida` or None for the image settings, `seed` is None or a seed ComfyUI accepts, and
        `steps` and `cfg` are None for the image settings or inside the range of docs/configuration.md. Sending the
        same `client_message_id` to the conversation again returns the first result and writes nothing.
        Raises NotFoundError for a missing or deleted conversation, ConflictError for an archived one, when the queue
        is full and when the id already names a message that is not an image request, and ValidationFailedError for
        a blank or long request, an unknown aspect or quality, a value out of range, or an outfit the card does not
        offer.
        """
        if not request.strip():
            raise ValidationFailedError("La richiesta è vuota.")
        if len(request) > MAX_REQUEST_CHARS:
            raise ValidationFailedError(f"La richiesta supera i {MAX_REQUEST_CHARS} caratteri.")
        _check_options(aspect, quality, seed, steps, cfg)
        now = to_utc_text(self._clock())

        def save(connection: Connection) -> SentMessage:
            conversation = _active_conversation(connection, conversation_id)
            if client_message_id is not None:
                stored = repository.find_by_client_id(connection, conversation_id, client_message_id)
                if stored is not None:
                    return self._replay(connection, conversation, stored)
            _check_outfit(connection, conversation, outfit)
            user = _add_message(
                connection,
                conversation_id,
                Author.USER,
                MessageStatus.COMPLETE,
                request,
                now,
                client_message_id=client_message_id,
            )
            reply = _add_message(
                connection, conversation_id, Author.CHARACTER, MessageStatus.QUEUED, "", now, kind="image"
            )
            assert reply.active_variant_id is not None
            payload: dict[str, Any] = {
                "user_message_id": user.id,
                "reply_message_id": reply.id,
                "variant_id": reply.active_variant_id,
                "request": request,
                "aspect": aspect,
                "outfit": outfit,
                "quality": quality,
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "purpose": "chat",
            }
            job = self._enqueue(connection, conversation, reply, payload, key=f"image_request:{user.id}", stage=None)
            return SentMessage(message=user, reply=_reload(connection, reply.id), job=job)

        return self._run(save)

    def request_asset(
        self,
        character_id: str,
        purpose: str,
        scene: str = "",
        *,
        aspect: str = "square",
        outfit: str | None = None,
        quality: str | None = None,
        seed: int | None = None,
    ) -> JobRef:
        """Queues an `image_request` job that draws the character outside any chat (character mode) and returns it.

        `purpose` is one of GENERATED_KINDS: when the job ends, the image joins the character's assets as that kind
        (ImageGenerationService). `scene` describes the picture; it may be blank except for `scene`, and then the
        default text of the purpose is drawn. The other values are those of `request`. No message is written, the
        image has no caption, and the job runs under the same stages as a chat image. Raises NotFoundError for an
        unknown character, ConflictError when the queue is full, and ValidationFailedError for an unknown purpose, a
        blank scene of the `scene` purpose, a scene over MAX_REQUEST_CHARS, or the errors of `request` for the other
        values.
        """
        if purpose not in GENERATED_KINDS:
            raise ValidationFailedError("Il tipo di immagine non è valido.")
        drawn = scene.strip() or DEFAULT_SCENES.get(purpose, "")
        if not drawn:
            raise ValidationFailedError("Descrivi la scena da generare.")
        if len(drawn) > MAX_REQUEST_CHARS:
            raise ValidationFailedError(f"La richiesta supera i {MAX_REQUEST_CHARS} caratteri.")
        _check_options(aspect, quality, seed, None, None)

        def save(connection: Connection) -> JobRef:
            stored = character_repository.get_card(connection, character_id)
            if stored is None:
                raise NotFoundError(_NO_CHARACTER)
            try:
                outfit_choices(stored.card.visual, outfit)
            except ValueError:
                raise ValidationFailedError(_NO_OUTFIT) from None
            payload: dict[str, Any] = {
                "request": drawn,
                "aspect": aspect,
                "outfit": outfit,
                "quality": quality,
                "seed": seed,
                "purpose": purpose,
            }
            job = self._queue.enqueue(
                connection, JobKind.IMAGE_REQUEST, payload, character_id=character_id, idempotency_key=None
            )
            return JobRef(
                id=job.id,
                kind=job.kind.value,
                status=job.status.value,
                queue_position=repository.queue_position(connection, job.id),
            )

        return self._run(save)

    def queue_proposed(self, connection: Connection, conversation_id: str, scene: str, *, key: str) -> SentMessage:
        """Adds the image message of an accepted proposal and queues its job, in the caller's transaction.

        Nothing of the user is saved: the character sends the image, so the returned `message` is the placeholder
        itself (status `queued`, one `streaming` variant), and `reply` is the same message. `scene` is drawn as the
        request, with the usual outfit choice left to the scene. `key` makes a repeat return the first job. Raises
        NotFoundError for a missing or deleted conversation, ConflictError for an archived one, and QueueFullError
        when the queue is full, which the caller turns into its own answer.
        """
        conversation = _active_conversation(connection, conversation_id)
        now = to_utc_text(self._clock())
        placeholder = _add_message(
            connection, conversation_id, Author.CHARACTER, MessageStatus.QUEUED, "", now, kind="image"
        )
        assert placeholder.active_variant_id is not None
        payload: dict[str, Any] = {
            "reply_message_id": placeholder.id,
            "variant_id": placeholder.active_variant_id,
            "request": scene,
            "aspect": "auto",
            "outfit": None,
            "quality": None,
            "seed": None,
            "purpose": "chat",
        }
        job = self._enqueue(connection, conversation, placeholder, payload, key=key, stage=None)
        return SentMessage(message=placeholder, reply=placeholder, job=job)

    def regenerate(self, image_id: str, *, keep_seed: bool = False) -> Regeneration:
        """Adds a `generated` variant to the image's message, makes it the active one and queues an `image_request`
        job that skips the `prompt` stage: it renders the stored prompt again, with a new seed unless `keep_seed`.

        The caption of the image stays the variant's text. The older variants stay selectable. Raises NotFoundError
        for an unknown or deleted image or message, and ConflictError when the image belongs to no conversation, the
        conversation is archived or the message is not its last visible one, the message is still being produced, or
        the queue is full.
        """
        now = to_utc_text(self._clock())

        def save(connection: Connection) -> Regeneration:
            row = image_repository.get(connection, image_id)
            if row is None or row["deleted_at"] is not None:
                raise NotFoundError(_NO_IMAGE)
            if row["message_id"] is None or row["conversation_id"] is None:
                raise ConflictError("L'immagine non appartiene a una conversazione.")
            message = repository.get_message(connection, row["message_id"])
            if message is None or message.deleted_at is not None:
                raise NotFoundError(_NO_IMAGE)
            conversation = _active_conversation(connection, message.conversation_id)
            last = repository.last_visible(connection, message.conversation_id)
            if last is None or last.id != message.id:
                raise ConflictError(_ONLY_LAST)
            if message.status in PENDING:
                raise ConflictError(_BUSY)
            variant_id = new_id()
            repository.insert_variant(
                connection,
                variant_id=variant_id,
                message_id=message.id,
                content="",
                content_hash=content_hash(""),
                source=VariantSource.GENERATED,
                status=VariantStatus.STREAMING,
                now=now,
            )
            repository.update_message(
                connection, message.id, content="", active_variant_id=variant_id, status=MessageStatus.QUEUED.value
            )
            width, height = final_size(row["quality_preset"], row["aspect"])
            payload: dict[str, Any] = {
                "reply_message_id": message.id,
                "variant_id": variant_id,
                "source_image_id": row["id"],
                "request": row["user_request"],
                "aspect": row["aspect"],
                "outfit": None,
                "quality": row["quality_preset"],
                "steps": row["steps"],
                "cfg": row["cfg"],
                "seed": row["seed"] if keep_seed else self._seed_source(),
                "purpose": row["purpose"],
                "caption": _caption_of(connection, row["id"]),
                "caption_checks": {},
                "scene": json.loads(row["scene_json"]),
                "scene_fallback": False,
                "positive": row["prompt_positive"],
                "negative": row["prompt_negative"],
                "outfit_key": row["outfit_key"],
                "width": width,
                "height": height,
            }
            job = self._enqueue(
                connection, conversation, message, payload, key=f"image_regenerate:{variant_id}", stage=STAGE_RENDER
            )
            return Regeneration(message=_reload(connection, message.id), variant_id=variant_id, job=job)

        return self._run(save)

    def get(self, image_id: str) -> dict[str, Any]:
        """Returns the metadata of a live image (persistence.images.document). Raises NotFoundError otherwise."""
        row = read(self._engine, lambda connection: image_repository.get(connection, image_id))
        if row is None or row["deleted_at"] is not None:
            raise NotFoundError(_NO_IMAGE)
        return image_repository.document(row)

    def media_file(self, image_id: str, *, thumbnail: bool) -> tuple[Path, str]:
        """Returns the real path and media type of the original of a live image, or of its WebP thumbnail.

        The path comes from the image row, never from the request, so a ComfyUI that is off changes nothing. Raises
        NotFoundError for an unknown or deleted image, a missing file, or a stored path that leaves the media root;
        the last case is logged as `media_read_refused`.
        """
        row = read(self._engine, lambda connection: image_repository.get(connection, image_id))
        if row is None or row["deleted_at"] is not None:
            raise NotFoundError(_NO_IMAGE)
        try:
            path = self._media.resolve(row["thumb_path"] if thumbnail else row["file_path"])
        except MediaPathError:
            logger.warning("media_read_refused")
            raise NotFoundError(_NO_IMAGE) from None
        if not path.is_file():
            raise NotFoundError(_NO_IMAGE)
        return path, "image/webp" if thumbnail else row["media_type"]

    def _replay(self, connection: Connection, conversation: Conversation, user: Message) -> SentMessage:
        """Returns the result of the earlier request that saved `user`: its image message and its job, which the
        idempotency key finds (the first attempt saved the messages and the job in one transaction, so the job exists
        and the empty payload is never stored). Raises ConflictError when `user` has no image message after it,
        because it was sent as text."""
        reply = repository.find_by_seq(connection, conversation.id, user.seq + 1)
        if reply is None or reply.kind not in ("image", "mixed"):
            raise ConflictError(_ALREADY_SENT)
        job = self._enqueue(connection, conversation, reply, {}, key=f"image_request:{user.id}", stage=None)
        return SentMessage(message=user, reply=reply, job=job)

    def _enqueue(
        self,
        connection: Connection,
        conversation: Conversation,
        message: Message,
        payload: dict[str, Any],
        *,
        key: str,
        stage: str | None,
    ) -> JobRef:
        job = self._queue.enqueue(
            connection,
            JobKind.IMAGE_REQUEST,
            payload,
            conversation_id=conversation.id,
            character_id=conversation.character_id,
            message_id=message.id,
            idempotency_key=key,
            stage=stage,
        )
        return JobRef(
            id=job.id,
            kind=job.kind.value,
            status=job.status.value,
            queue_position=repository.queue_position(connection, job.id),
        )

    def _run[T](self, save: Callable[[Connection], T]) -> T:
        """Returns `save` run in one write transaction. A full queue rolls the whole request back, so nothing stays
        saved, and raises ConflictError."""
        try:
            return write(self._engine, save)
        except QueueFullError:
            raise ConflictError(_QUEUE_FULL) from None


def _check_options(aspect: str, quality: str | None, seed: int | None, steps: int | None, cfg: float | None) -> None:
    """Raises ValidationFailedError for an unknown aspect or quality, or a seed, step count or CFG outside its range."""
    if aspect not in _ASPECTS:
        raise ValidationFailedError("Formato dell'immagine sconosciuto.")
    if quality is not None and quality not in _QUALITIES:
        raise ValidationFailedError("Qualità dell'immagine sconosciuta.")
    if seed is not None and (isinstance(seed, bool) or not 0 <= seed <= MAX_SEED):
        raise ValidationFailedError("Il seed è fuori dall'intervallo ammesso.")
    if steps is not None and (isinstance(steps, bool) or not MIN_STEPS <= steps <= MAX_STEPS):
        raise ValidationFailedError(f"I passi vanno da {MIN_STEPS} a {MAX_STEPS}.")
    if cfg is not None and (isinstance(cfg, bool) or not MIN_CFG <= cfg <= MAX_CFG):
        raise ValidationFailedError(f"Il CFG va da {MIN_CFG:g} a {MAX_CFG:g}.")


def _check_outfit(connection: Connection, conversation: Conversation, outfit: str | None) -> None:
    """Raises ValidationFailedError when the conversation's character does not offer `outfit`."""
    stored = character_repository.get_card(connection, conversation.character_id)
    assert stored is not None, "a conversation's character cannot be removed while the chat exists"
    try:
        outfit_choices(stored.card.visual, outfit)
    except ValueError:
        raise ValidationFailedError(_NO_OUTFIT) from None


def _active_conversation(connection: Connection, conversation_id: str) -> Conversation:
    conversation = conversation_repository.get(connection, conversation_id)
    if conversation is None or conversation.status is ConversationStatus.DELETED:
        raise NotFoundError(_NO_CHAT)
    if conversation.status is not ConversationStatus.ACTIVE:
        raise ConflictError("La conversazione è archiviata.")
    return conversation


def _reload(connection: Connection, message_id: str) -> Message:
    message = repository.get_message(connection, message_id)
    assert message is not None
    return message


def _caption_of(connection: Connection, image_id: str) -> str | None:
    """Returns the text of the variant that shows the image, which a regeneration keeps as its caption."""
    found = connection.execute(
        text("SELECT content FROM message_variants WHERE image_id = :id ORDER BY variant_no LIMIT 1"), {"id": image_id}
    ).scalar_one_or_none()
    return found or None


def _add_message(
    connection: Connection,
    conversation_id: str,
    author: Author,
    status: MessageStatus,
    content: str,
    now: str,
    *,
    kind: str = "text",
    client_message_id: str | None = None,
) -> Message:
    """Adds a message with its first variant, active, and returns it. Only a user message starts `sent`; a character
    message with no text yet gets a `streaming` variant that its job fills."""
    message_id = new_id()
    variant_id = new_id()
    repository.insert_message(
        connection,
        message_id=message_id,
        conversation_id=conversation_id,
        seq=repository.take_seq(connection, conversation_id, now),
        client_message_id=client_message_id,
        author=author,
        status=status,
        read_state="sent" if author is Author.USER else None,
        content=content,
        now=now,
    )
    repository.insert_variant(
        connection,
        variant_id=variant_id,
        message_id=message_id,
        content=content,
        content_hash=content_hash(content),
        source=VariantSource.USER if author is Author.USER else VariantSource.GENERATED,
        status=VariantStatus.COMPLETE if author is Author.USER else VariantStatus.STREAMING,
        now=now,
    )
    repository.update_message(connection, message_id, active_variant_id=variant_id, kind=kind)
    return _reload(connection, message_id)
