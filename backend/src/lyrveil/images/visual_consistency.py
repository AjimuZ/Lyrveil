"""VisualConsistencyService: the Visual PromptBuilder of docs/image-generation.md (ADR 0035, ADR 0036).

It filters the model's structured scene, derives the age band, renders the identity slots in a fixed order and joins
them with the outfit, the scene, the style and the preset's tags into the final positive and negative prompts. It is
pure and deterministic: the same visual profile, age, scene and preset always give the same prompts, byte for byte.
No text model writes identity tags, weights or prompt syntax, and every tag from a model or a user passes the
sanitizer. Prompts are private: the caller stores them but never logs them.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from lyrveil.catalog import ImagePreset
from lyrveil.consistency.content_guard import find_violation
from lyrveil.domain.card import MIN_AGE, SubjectTag, VisualProfile
from lyrveil.domain.errors import ContentPolicyBlockedError
from lyrveil.images.sanitizer import sanitize_tag
from lyrveil.images.tags import SCENE_CATEGORIES, TagVocabulary, load_vocabulary

Framing = Literal["close-up", "portrait", "upper body", "cowboy shot", "full body", "wide shot"]
Angle = Literal["straight-on", "from side", "from behind", "from above", "from below", "pov", "dutch angle"]
TimeOfDay = Literal["day", "sunrise", "sunset", "twilight", "night"]

# docs/image-generation.md (Caps and prompt chunks).
IDENTITY_CAP = 16
OUTFIT_CAP = 12
SCENE_CAP = 18
STYLE_CAP = 4
EXTRA_CAP = 6
MAX_SCENE_TAG_WORDS = 4
# ComfyUI encodes 75 tokens per chunk, plus start and end; SDXL's pooled vector comes from the first chunk only.
CHUNK_TOKENS = 75
# A tag the vocabulary does not list is counted at two tokens per word, which overestimates rather than under.
FREE_TAG_TOKENS_PER_WORD = 2

# ADR 0018: always appended to the negative prompt, whatever the profile, the card or the scene says.
SAFETY_NEGATIVE = ("child", "loli", "shota", "toddler", "underage", "minor", "nsfw", "explicit", "nude")
_SAFETY_WORDS = frozenset(SAFETY_NEGATIVE)

# The categories a scene can never set: the identity slots own them (scene filter, step 4).
IDENTITY_CATEGORIES = frozenset(
    {"hair_color", "hair_length", "hairstyle", "eye_color", "skin", "face", "body", "marks"}
)
# A free tag the vocabulary does not know is still an identity tag when it ends in one of these words, such as
# "long blonde hair": otherwise any colour or length the list lacks would override the identity.
_IDENTITY_HEAD_WORDS = frozenset({"hair", "eyes", "skin"})
# Scene filter, step 5: the locked visual fields whose category a scene cannot add to.
_LOCKED_SCENE_CATEGORIES = {"accessories": "accessories", "distinctive_marks": "marks"}

_BLOCKED = "La scena dell'immagine contiene un termine non consentito."


@dataclass(frozen=True)
class PromptPreset:
    """The tags of an image profile's preset (docs/image-generation.md, Final prompt)."""

    quality: tuple[str, ...]
    rating: str
    base_negative: tuple[str, ...]


def _tag_list(text: str) -> tuple[str, ...]:
    return tuple(tag.strip() for tag in text.split(","))


PROMPT_PRESETS: Mapping[ImagePreset, PromptPreset] = MappingProxyType(
    {
        "illustrious": PromptPreset(
            quality=_tag_list("masterpiece, best quality, amazing quality, absurdres"),
            rating="general",
            base_negative=_tag_list(
                "worst quality, bad quality, low quality, lowres, bad anatomy, bad hands, missing fingers, "
                "extra digits, jpeg artifacts, signature, watermark, text, username"
            ),
        ),
        # From the NoobAI-XL card, without `nsfw`, which the safety negative always holds.
        "noobai": PromptPreset(
            quality=_tag_list("masterpiece, best quality, newest, absurdres, highres"),
            rating="safe",
            base_negative=_tag_list(
                "worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, mutated hands, "
                "mammal, anthro, furry, ambiguous form, feral, semi-anthro"
            ),
        ),
    }
)

# The age band after `adult` (ADR 0035): the highest age of each band, then the tags for each subject tag.
_AGE_BANDS: tuple[tuple[int, Mapping[SubjectTag, tuple[str, ...]]], ...] = (
    (29, {"1girl": ("woman",), "1boy": ("man",), "1other": ("person",)}),
    (44, {"1girl": ("mature female",), "1boy": ("mature male",), "1other": ("mature",)}),
    (
        64,
        {
            "1girl": ("mature female", "middle-aged"),
            "1boy": ("mature male", "middle-aged"),
            "1other": ("mature", "middle-aged"),
        },
    ),
)
_OLDEST_BAND: Mapping[SubjectTag, tuple[str, ...]] = {
    "1girl": ("old woman", "wrinkles"),
    "1boy": ("old man", "wrinkles"),
    "1other": ("old", "wrinkles"),
}


class ImageScene(BaseModel):
    """The structured scene the text model writes (ADR 0036). Its tags are untrusted until `filter_scene` has run.

    `outfit` is "usual", a named outfit key, or "scene", in which case `scene_clothing` describes the clothes. The
    grammar of each request narrows `outfit`; the builder still falls back to the usual outfit for any other value.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    framing: Framing
    angle: Angle | None
    expression: Annotated[list[str], Field(max_length=3)]
    pose: Annotated[list[str], Field(max_length=4)]
    outfit: str
    scene_clothing: Annotated[list[str], Field(max_length=6)]
    location: Annotated[list[str], Field(max_length=6)]
    time_of_day: TimeOfDay | None
    lighting: Annotated[list[str], Field(max_length=3)]
    objects: Annotated[list[str], Field(max_length=4)]
    extra_negative: Annotated[list[str], Field(max_length=6)]


class Removal(StrEnum):
    """Why the scene filter dropped a tag, by the step that dropped it (docs/image-generation.md, Scene filter)."""

    EMPTY = "empty"  # Step 1: nothing was left after the sanitizer.
    TOO_LONG = "too_long"  # Step 2: more than 4 words.
    UNKNOWN = "unknown"  # Step 2: a vocabulary list (expression, pose, lighting) holds a tag the vocabulary lacks.
    DUPLICATE = "duplicate"  # Step 2: the scene already holds the tag.
    IN_IDENTITY = "in_identity"  # Step 2: the identity, outfit, style or extra already holds the tag.
    IDENTITY_CATEGORY = "identity_category"  # Step 4: hair, eyes, skin, face, body or marks.
    LOCKED = "locked"  # Step 5: the category of a locked `accessories` or `distinctive_marks` field.


@dataclass(frozen=True)
class RemovedTag:
    """A dropped scene tag: its list in ImageScene, the tag as sanitized, and why. Private text, never logged."""

    field: str
    tag: str
    reason: Removal


@dataclass(frozen=True)
class FilteredScene:
    """The scene tags the prompt may use, sanitized and in their original order, and the tags the filter dropped."""

    scene_clothing: tuple[str, ...]
    expression: tuple[str, ...]
    pose: tuple[str, ...]
    location: tuple[str, ...]
    lighting: tuple[str, ...]
    objects: tuple[str, ...]
    extra_negative: tuple[str, ...]
    removed: tuple[RemovedTag, ...]


@dataclass(frozen=True)
class FinalPrompt:
    """The prompts of one image.

    `identity` is the start of `positive`, from the quality tags to the last identity slot: it depends on the visual
    profile, the age and the preset only, never on the scene. `outfit_key` is the outfit used: "usual", a named key or
    "scene". `tokens` estimates the CLIP tokens of `positive` from the vocabulary's counts, two per word for other
    tags, plus one per comma; `identity_tokens` is the same count up to the end of `identity`, and
    `identity_in_first_chunk` says whether it is within the first 75-token chunk.
    """

    positive: str
    negative: str
    identity: str
    outfit_key: str
    tokens: int
    identity_tokens: int
    identity_in_first_chunk: bool
    removed: tuple[RemovedTag, ...]


def age_band(age: int, subject: SubjectTag) -> tuple[str, ...]:
    """Returns the tags that follow `adult` for a character of `age` drawn as `subject` (ADR 0035). Requires an adult
    age, as the card guarantees; raises ValueError below 18."""
    if age < MIN_AGE:
        raise ValueError("every character is an adult")
    for highest, tags in _AGE_BANDS:
        if age <= highest:
            return tags[subject]
    return _OLDEST_BAND[subject]


def _unique(tags: Iterable[str]) -> list[str]:
    """Returns the non-empty tags in order, each once."""
    return list(dict.fromkeys(tag for tag in tags if tag))


def _clean(tags: Iterable[str | None]) -> list[str]:
    """Returns stored tags through the sanitizer, so that a card saved before a rule changed cannot add syntax."""
    return _unique(sanitize_tag(tag) for tag in tags if tag)


def _is_blocked(tag: str) -> bool:
    """A blocked term: a minor for the content guard, or a word of the safety negative, which a positive tag would
    fight."""
    return find_violation(tag) is not None or any(word in _SAFETY_WORDS for word in tag.split())


class VisualConsistencyService:
    """Builds image prompts from a character's visual profile and a structured scene, with one tag vocabulary."""

    def __init__(self, vocabulary: TagVocabulary | None = None) -> None:
        self._vocabulary = vocabulary or load_vocabulary()

    def identity_tags(self, visual: VisualProfile) -> tuple[str, ...]:
        """Returns the identity slots in the fixed order of ADR 0035, at most IDENTITY_CAP tags: hair color, hair
        length, hairstyle, eye color, skin, face, body, marks. The scene and the text model play no part."""
        slots = visual.identity_en
        ordered = [
            slots.hair_color,
            slots.hair_length,
            *slots.hairstyle,
            slots.eye_color,
            slots.skin,
            *slots.face,
            *slots.body,
            *slots.marks,
        ]
        return tuple(_clean(ordered)[:IDENTITY_CAP])

    def identity_segment(self, visual: VisualProfile, age: int, preset: ImagePreset) -> tuple[str, ...]:
        """Returns the tags that open every positive prompt of the character: quality tags, rating, subject tag,
        `adult`, the age band and the identity slots, each once."""
        tags = PROMPT_PRESETS[preset]
        return tuple(
            _unique(
                [
                    *tags.quality,
                    tags.rating,
                    visual.subject_tag,
                    "adult",
                    *age_band(age, visual.subject_tag),
                    *self.identity_tags(visual),
                ]
            )
        )

    def _is_identity_category(self, tag: str) -> bool:
        categories = self._vocabulary.categories_of(tag)
        if categories:
            return bool(categories & IDENTITY_CATEGORIES)
        # Digits and periods a stripped weight left behind ("red hair1.5") do not hide the head word.
        return tag.split()[-1].rstrip("0123456789.") in _IDENTITY_HEAD_WORDS

    def filter_scene(self, scene: ImageScene, visual: VisualProfile) -> FilteredScene:
        """Returns the tags of `scene` that the prompt may use. Lists are read in prompt order (scene clothing, only
        when the scene chooses the outfit "scene", then expression, pose, location, lighting, objects), and each tag
        goes through the steps of docs/image-generation.md (Scene filter) in order, the first step that drops it
        being the one recorded:

        1. the sanitizer; an empty result is dropped;
        2. more than 4 words, a tag of a vocabulary list that the vocabulary lacks, a tag the scene already holds, and
           a tag the identity, outfit, accessories, style or extra already hold are dropped;
        3. a blocked term raises ContentPolicyBlockedError, whose detail never quotes the tag;
        4. a tag of an identity category (hair, eyes, skin, face, body, marks) is dropped, by the vocabulary's tags
           and Italian aliases, or for a tag the vocabulary does not know, by a last word "hair", "eyes" or "skin"
           (trailing digits and periods ignored);
        5. with `accessories` or `distinctive_marks` locked, a tag of that category is dropped.

        `extra_negative` goes through steps 1 and 2 only, as the negative may name what an image must avoid; a tag
        that would negate the identity (step 2's identity tags, or an identity category) is dropped too.
        """
        uses_scene_clothing = scene.outfit == "scene" and "clothing" not in visual.locked_fields
        taken = set(self._fixed_tags(visual, None if uses_scene_clothing else scene.outfit))
        seen: set[str] = set()
        removed: list[RemovedTag] = []
        locked = {category for name, category in _LOCKED_SCENE_CATEGORIES.items() if name in visual.locked_fields}

        def keep(field: str, raw_tags: list[str]) -> tuple[str, ...]:
            kept: list[str] = []
            for raw in raw_tags:
                tag = sanitize_tag(raw)
                reason = self._reason(field, tag, seen, taken, locked)
                if reason is None:
                    seen.add(tag)
                    kept.append(tag)
                else:
                    removed.append(RemovedTag(field=field, tag=tag, reason=reason))
            return tuple(kept)

        clothing = keep("scene_clothing", scene.scene_clothing) if uses_scene_clothing else ()
        expression = keep("expression", scene.expression)
        pose = keep("pose", scene.pose)
        location = keep("location", scene.location)
        lighting = keep("lighting", scene.lighting)
        objects = keep("objects", scene.objects)
        negative: list[str] = []
        for raw in scene.extra_negative:
            tag = sanitize_tag(raw)
            reason = self._negative_reason(tag, negative, taken | seen)
            if reason is None:
                negative.append(tag)
            else:
                removed.append(RemovedTag(field="extra_negative", tag=tag, reason=reason))
        return FilteredScene(
            scene_clothing=clothing,
            expression=expression,
            pose=pose,
            location=location,
            lighting=lighting,
            objects=objects,
            extra_negative=tuple(negative),
            removed=tuple(removed),
        )

    def _reason(self, field: str, tag: str, seen: set[str], taken: set[str], locked: set[str]) -> Removal | None:
        """Returns why the filter drops `tag`, by the first step that drops it, or None to keep it. Raises
        ContentPolicyBlockedError at step 3."""
        if not tag:
            return Removal.EMPTY
        if len(tag.split()) > MAX_SCENE_TAG_WORDS:
            return Removal.TOO_LONG
        if field in SCENE_CATEGORIES and not self._vocabulary.contains(field, tag):
            return Removal.UNKNOWN
        if tag in seen:
            return Removal.DUPLICATE
        if tag in taken:
            return Removal.IN_IDENTITY
        if _is_blocked(tag):
            raise ContentPolicyBlockedError(_BLOCKED)
        if self._is_identity_category(tag):
            return Removal.IDENTITY_CATEGORY
        if self._vocabulary.categories_of(tag) & locked:
            return Removal.LOCKED
        return None

    def _negative_reason(self, tag: str, kept: list[str], positive: set[str]) -> Removal | None:
        if not tag:
            return Removal.EMPTY
        if len(tag.split()) > MAX_SCENE_TAG_WORDS:
            return Removal.TOO_LONG
        if tag in kept:
            return Removal.DUPLICATE
        if tag in positive:
            return Removal.IN_IDENTITY
        if self._is_identity_category(tag):
            return Removal.IDENTITY_CATEGORY
        return None

    def _fixed_tags(self, visual: VisualProfile, outfit_key: str | None) -> list[str]:
        """The tags the card adds around the scene: identity slots, the stored outfit `outfit_key` names (none for a
        scene outfit), accessories, style and extra."""
        slots = visual.identity_en
        outfit = [] if outfit_key is None else self._stored_outfit(visual, outfit_key)[1]
        return _clean([*self.identity_tags(visual), *outfit, *slots.accessories, *slots.style, *slots.extra])

    @staticmethod
    def _stored_outfit(visual: VisualProfile, key: str) -> tuple[str, list[str]]:
        """Returns the named outfit `key` and its tags, or the usual outfit for "usual" and for any other key."""
        for outfit in visual.outfits:
            if outfit.key == key:
                return outfit.key, _clean(outfit.tags_en)
        return "usual", _clean(visual.usual_outfit_en)

    def _outfit(self, visual: VisualProfile, scene: ImageScene, filtered: FilteredScene) -> tuple[str, list[str]]:
        """Returns the outfit key used and its tags. "scene" needs `clothing` unlocked and at least one kept clothing
        tag; an unknown key, or a scene outfit that cannot be used, falls back to the usual outfit."""
        if scene.outfit == "scene" and filtered.scene_clothing:
            return "scene", list(filtered.scene_clothing)
        return self._stored_outfit(visual, scene.outfit)

    def _tokens(self, tags: list[str]) -> int:
        """The token estimate of tags joined by ", ": vocabulary counts, two per word otherwise, one per comma."""
        words = 0
        for tag in tags:
            counted = self._vocabulary.clip_tokens(tag)
            words += counted if counted is not None else FREE_TAG_TOKENS_PER_WORD * len(tag.split())
        return words + max(len(tags) - 1, 0)

    def build(self, visual: VisualProfile, age: int, scene: ImageScene, preset: ImagePreset) -> FinalPrompt:
        """Returns the prompts of one image, in the fixed order of docs/image-generation.md (Final prompt):

        positive: quality tags, rating, subject tag, `adult`, age band, identity slots (at most 16), outfit and
        accessories (at most 12), scene (expression, pose, framing, angle, location, time of day, lighting, objects;
        at most 18), style (at most 4), extra (at most 6). A tag appears once, at its first place.
        negative: base negative, the safety negative (always, every term), the persistent negative, then the scene's
        extra negative after the filter. A term appears once, at its first place.

        Raises ContentPolicyBlockedError when a scene tag is a blocked term. The same arguments always give the same
        prompts.
        """
        filtered = self.filter_scene(scene, visual)
        identity = list(self.identity_segment(visual, age, preset))
        outfit_key, outfit = self._outfit(visual, scene, filtered)
        dressed = _unique([*outfit, *_clean(visual.identity_en.accessories)])[:OUTFIT_CAP]
        scene_tags = _unique(
            [
                *filtered.expression,
                *filtered.pose,
                scene.framing,
                scene.angle or "",
                *filtered.location,
                scene.time_of_day or "",
                *filtered.lighting,
                *filtered.objects,
            ]
        )[:SCENE_CAP]
        style = _clean(visual.identity_en.style)[:STYLE_CAP]
        extra = _clean(visual.identity_en.extra)[:EXTRA_CAP]
        positive = _unique([*identity, *dressed, *scene_tags, *style, *extra])
        # The scene cannot negate what the positive holds, such as the age band.
        removed = list(filtered.removed)
        extra_negative: list[str] = []
        for tag in filtered.extra_negative:
            if tag in positive:
                removed.append(RemovedTag(field="extra_negative", tag=tag, reason=Removal.IN_IDENTITY))
            else:
                extra_negative.append(tag)
        persistent = _clean(visual.persistent_negative_en.split(","))
        preset_tags = PROMPT_PRESETS[preset]
        negative = _unique([*preset_tags.base_negative, *SAFETY_NEGATIVE, *persistent, *extra_negative])
        identity_tokens = self._tokens(identity)
        return FinalPrompt(
            positive=", ".join(positive),
            negative=", ".join(negative),
            identity=", ".join(identity),
            outfit_key=outfit_key,
            tokens=self._tokens(positive),
            identity_tokens=identity_tokens,
            identity_in_first_chunk=identity_tokens <= CHUNK_TOKENS,
            removed=tuple(removed),
        )
