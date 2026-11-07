"""The tag vocabulary of ADR 0035, loaded from `vocabulary/tags.v1.json` and checked against the sanitizer."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TypeGuard

from lyrveil.images.sanitizer import sanitize_tag

VOCABULARY_PATH = Path(__file__).parent / "vocabulary" / "tags.v1.json"
# The categories the character card validates (docs/image-generation.md, Tag vocabulary).
CARD_CATEGORIES = frozenset(
    {"hair_color", "hair_length", "eye_color", "skin", "hairstyle", "face", "body", "marks", "accessories", "style"}
)
# The closed lists of ImageScene, which the scene filter accepts only from the vocabulary.
SCENE_CATEGORIES = frozenset({"expression", "pose", "lighting"})
CATEGORIES = CARD_CATEGORIES | SCENE_CATEGORIES


class VocabularyError(Exception):
    """The vocabulary file breaks a rule of ADR 0035; it is a packaging fault, never a user error."""


@dataclass(frozen=True)
class TagEntry:
    """`tag` is the English tag as written in the prompt; `aliases_it` are the Italian words that map to it;
    `clip_tokens` is the number of tokens ComfyUI's SDXL tokenizer gives the tag, separators excluded, always 1 or
    more. The clip-tokens target of docker/image/Dockerfile writes it and T-IMG-16 checks it."""

    tag: str
    category: str
    aliases_it: tuple[str, ...]
    clip_tokens: int


class TagVocabulary:
    """The entries of the file, indexed by category. Immutable once built."""

    def __init__(self, entries: tuple[TagEntry, ...]) -> None:
        self._entries = entries
        grouped: dict[str, list[str]] = {}
        categories: dict[str, set[str]] = {}
        tokens: dict[str, int] = {}
        for entry in entries:
            grouped.setdefault(entry.category, []).append(entry.tag)
            # A tag and each Italian alias, as the sanitizer writes them, name the entry's category.
            for word in (entry.tag, *(sanitize_tag(alias) for alias in entry.aliases_it)):
                categories.setdefault(word, set()).add(entry.category)
            tokens[entry.tag] = max(tokens.get(entry.tag, 0), entry.clip_tokens)
        self._ordered: Mapping[str, tuple[str, ...]] = {category: tuple(tags) for category, tags in grouped.items()}
        self._tags: Mapping[str, frozenset[str]] = {category: frozenset(tags) for category, tags in grouped.items()}
        self._categories: Mapping[str, frozenset[str]] = {word: frozenset(names) for word, names in categories.items()}
        self._tokens: Mapping[str, int] = tokens

    @property
    def entries(self) -> tuple[TagEntry, ...]:
        return self._entries

    def contains(self, category: str, tag: str) -> bool:
        """Returns whether `tag`, exactly as written, is listed under `category`."""
        return tag in self._tags.get(category, frozenset())

    def tags(self, category: str) -> frozenset[str]:
        """Returns the tags of `category`, empty for a category the file does not hold."""
        return self._tags.get(category, frozenset())

    def ordered(self, category: str) -> tuple[str, ...]:
        """Returns the tags of `category` in file order, empty for a category the file does not hold. Grammars use
        this order, so they are the same on every run."""
        return self._ordered.get(category, ())

    def categories_of(self, tag: str) -> frozenset[str]:
        """Returns the categories whose entries have `tag` as their tag or as a sanitized Italian alias; empty for a
        word the vocabulary does not know. Exact match only: `tall building` is not `tall`."""
        return self._categories.get(tag, frozenset())

    def clip_tokens(self, tag: str) -> int | None:
        """Returns the CLIP token count of a vocabulary tag, or None for a tag the file does not list."""
        return self._tokens.get(tag)


def _is_alias_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and bool(value) and all(isinstance(alias, str) and alias for alias in value)


def parse_vocabulary(document: object) -> TagVocabulary:
    """Returns the vocabulary of a parsed file.

    Raises VocabularyError for a missing or wrong field, a category outside CATEGORIES, a tag listed twice in the
    same category, a tag that the sanitizer would change (so no tag needs an escape in a prompt), or a `clip_tokens`
    that is not a whole number at least the tag's word count (CLIP never encodes a word in fewer than one token).
    """
    if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("tags"), list):
        raise VocabularyError("the vocabulary needs version 1 and a list of tags")
    entries: list[TagEntry] = []
    seen: set[tuple[str, str]] = set()
    for item in document["tags"]:
        if not isinstance(item, dict):
            raise VocabularyError("every tag entry must be an object")
        tag, category, aliases = item.get("tag"), item.get("category"), item.get("aliases_it")
        clip_tokens = item.get("clip_tokens")
        if not isinstance(tag, str) or not tag:
            raise VocabularyError("every entry needs a tag")
        if category not in CATEGORIES:
            raise VocabularyError(f"{tag!r} has an unknown category")
        if not _is_alias_list(aliases):
            raise VocabularyError(f"{tag!r} needs a non-empty list of Italian aliases")
        if sanitize_tag(tag) != tag:
            raise VocabularyError(f"{tag!r} is not written as the sanitizer keeps it")
        # bool is an int in Python, so it is excluded by name.
        if isinstance(clip_tokens, bool) or not isinstance(clip_tokens, int) or clip_tokens < len(tag.split()):
            raise VocabularyError(f"{tag!r} needs its clip_tokens count")
        if (category, tag) in seen:
            raise VocabularyError(f"{tag!r} is listed twice under {category}")
        seen.add((category, tag))
        entries.append(TagEntry(tag=tag, category=category, aliases_it=tuple(aliases), clip_tokens=clip_tokens))
    return TagVocabulary(tuple(entries))


@lru_cache(maxsize=1)
def load_vocabulary() -> TagVocabulary:
    """Returns the packaged vocabulary, read once. Raises VocabularyError when the file is invalid."""
    return parse_vocabulary(json.loads(VOCABULARY_PATH.read_text(encoding="utf-8")))
