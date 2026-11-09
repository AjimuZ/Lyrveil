"""The `prompt` stage of an image request (ADR 0036; docs/image-generation.md, Job stages, Caption and Visual
PromptBuilder): the caption in the character's voice, then the structured scene, then the final prompt and the size.

Both text calls run under the caller's llm lease, before the GPU changes hands, and the caption goes first, while
the chat prompt is still in the runtime's cache. The caption takes the reply path: the chat's prompt from
ContextBuilder with one extra line, the reply samplers, the V2 and V3 cut, the card's normalizer and the reply checks.
A caption that fails leaves the image without text. The scene is generated under a grammar built for the request,
with one repair, and an output still invalid after it gives the fallback scene. No caption, scene or prompt text is
ever logged.

Delivered here: the stage's work. The `image_request` job that runs it, chooses the seed and stores what it returns
is lyrveil.services.image_generation.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import ValidationError

from lyrveil.catalog import ImagePreset, TextTemplate
from lyrveil.consistency.checks import (
    PREVIOUS_REPLIES,
    STOPPING,
    CheckRules,
    SentenceSplitter,
    TurnCutoff,
    check_reply,
    first_stop,
)
from lyrveil.consistency.normalizer import NormalizerRules, ReplyNormalizer
from lyrveil.context.blocks import image_caption_line
from lyrveil.context.builder import ContextBuilder, ContextRequest, ContextSettings
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.messages import Author
from lyrveil.images.tags import TagVocabulary, load_vocabulary
from lyrveil.images.visual_consistency import FinalPrompt, ImageScene, VisualConsistencyService
from lyrveil.images.workflows import Aspect, QualityPreset, RequestedAspect, final_size, resolve_aspect
from lyrveil.logs import get_logger
from lyrveil.ports.memory_retriever import MemoryRetriever
from lyrveil.ports.text_runtime import FinishReason, RuntimeStreamDroppedError, TextRuntime
from lyrveil.prompts import image_scene
from lyrveil.prompts.formatter import formatter_for
from lyrveil.prompts.image_scene import RecentMessage, fallback_scene, outfit_choices, scene_grammar
from lyrveil.prompts.structured import (
    MAX_ATTEMPTS,
    OutputRejectedError,
    StructuredOutputInvalidError,
    generate_structured,
)
from lyrveil.services.card_rules import describe_error
from lyrveil.services.chat_inference import ReplySettings, reply_request

# docs/image-generation.md (Caption): the caption's `max_length` is the card's band, at most this.
CAPTION_MAX_LENGTH = 120
# Caption.failure when no caption was attempted: the image has no chat, or the chat has no user message to answer.
NO_CHAT = "no_chat"
# Caption.failure when someone else aborted the caption's generation.
ABORTED = "aborted"
# Caption.failure for an empty caption, the reply check of the same meaning.
EMPTY = "V1"
_UNKNOWN_OUTFIT = "La scena nomina un abito che la richiesta non ammette."

logger = get_logger(__name__)


@dataclass(frozen=True)
class Caption:
    """The text of the image's message, or None when the caption failed.

    `failure` says why, None on success: NO_CHAT, ABORTED, EMPTY (V1), or the reply check that refused a sentence
    (V4, V6, V10 or V11). `checks` is the record of the generation, `{"normalizer": counts, "checks": counts}`, with
    only the rules and checks that fired; empty when nothing was generated. It never holds text.
    """

    text: str | None
    failure: str | None
    checks: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedScene:
    """The scene the image is drawn with. `fallback` tells that the model's output and its repair were refused, and
    then `scene` is fallback_scene's; `attempts` counts the generations, 1 or 2."""

    scene: ImageScene
    attempts: int
    fallback: bool


@dataclass(frozen=True)
class ImagePromptRequest:
    """What one image asks for: the character's card, the user's request as written, the image profile's prompt
    preset, the quality preset, the aspect (`auto` by default) and the outfit the user picked in the form, if any."""

    card: CharacterCard
    request: str
    preset: ImagePreset
    quality: QualityPreset
    aspect: RequestedAspect = "auto"
    outfit: str | None = None


@dataclass(frozen=True)
class PreparedImage:
    """What the `prompt` stage leaves for the render: the caption, the scene, the final prompts, the aspect the
    request resolved to and the saved image's size."""

    caption: Caption
    scene: GeneratedScene
    prompt: FinalPrompt
    aspect: Aspect
    width: int
    height: int


def _recent(chat: ContextRequest | None) -> list[RecentMessage]:
    """The chat's history and current message, oldest first, as the scene prompt reads them."""
    if chat is None:
        return []
    recent: list[RecentMessage] = []
    for message in chat.messages:
        author: Literal["user", "character"] = "user" if message.author is Author.USER else "character"
        recent.append(RecentMessage(author, message.content))
    return recent


class ImagePromptStage:
    """Runs the `prompt` stage of one image at a time with `runtime`, which the caller's llm lease covers.

    `retriever` finds the memories of the caption's prompt, as for a reply; `settings` are the reply settings, whose
    template family, samplers and user the caption uses, and whose template family formats the scene prompt too.
    """

    def __init__(
        self,
        runtime: TextRuntime,
        retriever: MemoryRetriever,
        settings: ReplySettings | None = None,
        *,
        context: ContextSettings | None = None,
        vocabulary: TagVocabulary | None = None,
    ) -> None:
        self._runtime = runtime
        self._settings = settings or ReplySettings()
        self._builder = ContextBuilder(runtime, retriever, context)
        self._vocabulary = vocabulary or load_vocabulary()
        self._visual = VisualConsistencyService(self._vocabulary)

    async def run(self, image: ImagePromptRequest, chat: ContextRequest | None, genkey: str) -> PreparedImage:
        """Returns the caption, the scene, the final prompt and the size of `image`.

        `chat` is the reply context of the image's message (load_context_request at its seq), or None for an image
        outside a chat or a chat with no user message, which then gets no caption. The caption is generated first
        (write_caption), the scene second (generate_scene), then VisualConsistencyService.build gives the prompts,
        and the aspect `auto` follows the scene's framing. Raises ValueError when `image.outfit` is not one the card
        offers, before any generation; ContentPolicyBlockedError when a scene tag is a blocked term; and lets a
        TextRuntimeError through for the scheduler to retry the stage.
        """
        outfits = outfit_choices(image.card.visual, image.outfit)
        if chat is None:
            caption = Caption(text=None, failure=NO_CHAT)
        else:
            caption = await self.write_caption(chat, image.request, genkey)
        language = chat.language if chat is not None else image.card.identity.primary_language
        scene = await self.generate_scene(image, _recent(chat), language, outfits, genkey)
        prompt = self._visual.build(image.card.visual, image.card.identity.age, scene.scene, image.preset)
        aspect = resolve_aspect(image.aspect, scene.scene.framing)
        width, height = final_size(image.quality, aspect)
        return PreparedImage(caption=caption, scene=scene, prompt=prompt, aspect=aspect, width=width, height=height)

    async def write_caption(self, chat: ContextRequest, request: str, genkey: str) -> Caption:
        """Generates the caption of an image that `request` describes, in the character's voice.

        The prompt is the chat's reply prompt, with image_caption_line after the anchor and the card's length band
        capped at CAPTION_MAX_LENGTH (and by `llm.max_length`). One generation streams through the V2 and V3 cut and
        the normalizer, and its sentences pass the reply checks: a hit of V4, V6, V10 or V11 aborts it and fails the
        caption, and so does an empty text or a generation someone else aborted. There is no corrective generation,
        since the image does not need a caption. The other checks are recorded, as for a reply. Raises
        RuntimeStreamDroppedError when the stream ends without its final chunk, and lets other runtime errors through.
        """
        settings = self._settings
        formatter = formatter_for(settings.template)
        asked = replace(chat, extra_line=image_caption_line(chat.user, request))
        assembly = await self._builder.build(asked, formatter, min(settings.max_length, CAPTION_MAX_LENGTH))
        text_request = reply_request(assembly, formatter, settings, genkey)
        rules = CheckRules.for_chat(chat.card, chat.user, chat.language)
        cutoff = TurnCutoff(formatter.control_texts(), chat.user.display_name)
        normalizer = ReplyNormalizer(NormalizerRules.for_card(chat.card))
        sentences = SentenceSplitter()
        finish: FinishReason | None = None
        stopped: str | None = None
        ended = False  # This call asked the runtime to abort: the rest of the stream is dropped.
        async for chunk in self._runtime.stream(text_request):
            if chunk.final:
                finish = chunk.finish_reason
                continue
            if ended:
                continue
            released = normalizer.feed(cutoff.feed(chunk.token))
            stopped = first_stop(sentences.feed(released), rules, STOPPING)
            if stopped is not None or cutoff.cut is not None:
                ended = True
                await self._runtime.abort(genkey)
        if finish is None:
            raise RuntimeStreamDroppedError("the stream ended without its final chunk")
        if stopped is not None:
            return self._failed(stopped, {"normalizer": {}, "checks": {stopped: 1}})
        if cutoff.cut is not None:
            finish = "stop"
        held = normalizer.feed(cutoff.finish())
        normalized = normalizer.finish(finish)
        stopped = first_stop([*sentences.feed(held + normalized.tail), *sentences.finish()], rules, STOPPING)
        if stopped is not None:
            return self._failed(stopped, {"normalizer": dict(normalized.fired), "checks": {stopped: 1}})
        cut: dict[str, int] = {} if cutoff.cut is None else {cutoff.cut: 1}
        if finish == "aborted":
            return self._failed(ABORTED, {"normalizer": dict(normalized.fired), "checks": cut})
        text = normalized.text.strip()
        if not text:
            return self._failed(EMPTY, {"normalizer": dict(normalized.fired), "checks": cut})
        previous = [message.content for message in chat.history if message.author is Author.CHARACTER]
        found = check_reply(text, rules, previous=previous[-PREVIOUS_REPLIES:], user_message=chat.current_text)
        checks = dict(sorted({**found, **cut}.items(), key=lambda item: int(item[0][1:])))
        return Caption(text=text, failure=None, checks={"normalizer": dict(normalized.fired), "checks": checks})

    @staticmethod
    def _failed(failure: str, checks: Mapping[str, object]) -> Caption:
        logger.info("image_caption_failed", error_code=failure)
        return Caption(text=None, failure=failure, checks=checks)

    async def generate_scene(
        self,
        image: ImagePromptRequest,
        recent: Sequence[RecentMessage],
        language: str,
        outfits: Sequence[str],
        genkey: str,
    ) -> GeneratedScene:
        """Generates the ImageScene of `image` under scene_grammar for `outfits` (outfit_choices), with the one repair
        of generate_structured.

        The output must validate as ImageScene and name one of `outfits`; the grammar already allows nothing else, and
        the scene filter checks the tags later. When the output and its repair are both refused, the scene is
        fallback_scene of the request, with the user's outfit when the form picked one and the usual outfit
        otherwise. Lets a TextRuntimeError through.
        """
        template: TextTemplate = self._settings.template
        vocabulary = self._vocabulary

        def validate(raw: dict[str, Any]) -> ImageScene:
            try:
                scene = ImageScene.model_validate(raw)
            except ValidationError as error:
                raise OutputRejectedError(describe_error(error)) from None
            if scene.outfit not in outfits:
                raise OutputRejectedError(_UNKNOWN_OUTFIT)
            return scene

        try:
            generated = await generate_structured(
                self._runtime,
                prompt=lambda repair: image_scene.render_prompt(
                    template,
                    visual=image.card.visual,
                    setting=image.card.identity.setting,
                    recent=recent,
                    request=image.request,
                    language=language,
                    outfits=outfits,
                    vocabulary=vocabulary,
                    repair=repair,
                ),
                grammar=scene_grammar(vocabulary, outfits),
                validate=validate,
                genkey=genkey,
                max_length=image_scene.MAX_LENGTH,
                temperature=image_scene.TEMPERATURE,
            )
        except StructuredOutputInvalidError:
            logger.info("image_scene_fallback", attempt_count=MAX_ATTEMPTS)
            outfit = image.outfit if image.outfit is not None else image_scene.USUAL_OUTFIT
            return GeneratedScene(fallback_scene(image.request, vocabulary, outfit), MAX_ATTEMPTS, fallback=True)
        return GeneratedScene(generated.value, generated.attempts, fallback=False)
