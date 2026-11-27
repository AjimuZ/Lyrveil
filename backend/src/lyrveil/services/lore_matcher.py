"""Decides which lore entries a window of messages activates, by keywords alone (ADR 0049, Activation). Pure: no
database, no model, no regular expression and no recursion."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from lyrveil.domain.lore import LoreEntry
from lyrveil.memory.text import language_code, stems


class MatchReason(StrEnum):
    """Why an entry is active or not. The first two are active; every other value is the reason it stayed inactive."""

    MATCHED = "matched"
    CONSTANT = "constant"
    DISABLED = "disabled"
    NO_KEYS = "no_keys"
    NO_KEY_MATCHED = "no_key_matched"
    SECONDARY_NOT_MATCHED = "secondary_not_matched"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class EntryMatch:
    """The outcome for one entry. `matched_key` and `matched_secondary_key` are the texts, as typed, of the first key of
    each list that matched (None when none did or the list does not apply); `excluded_key` is the exclude key that
    deactivated the entry. A constant entry has none of the three."""

    entry_id: str
    active: bool
    reason: MatchReason
    matched_key: str | None = None
    matched_secondary_key: str | None = None
    excluded_key: str | None = None


def _is_word_char(char: str) -> bool:
    """Letters and digits only, the characters `lyrveil.memory.text.stems` keeps in a word."""
    return char.isalnum()


def _contains_exact(text: str, key: str) -> bool:
    """Returns whether `key` appears in `text` with the same letters and case, not inside a longer word. A word
    boundary is required only where the key itself starts or ends with a letter or digit, so `Aldara!` still matches in
    `Aldara!!`."""
    start = text.find(key)
    while start != -1:
        end = start + len(key)
        before_ok = start == 0 or not _is_word_char(key[0]) or not _is_word_char(text[start - 1])
        after_ok = end == len(text) or not _is_word_char(key[-1]) or not _is_word_char(text[end])
        if before_ok and after_ok:
            return True
        start = text.find(key, start + 1)
    return False


def _contains_sequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """Returns whether `needle` appears in `haystack` as an unbroken run, in order. An empty needle never matches."""
    size = len(needle)
    if size == 0 or size > len(haystack):
        return False
    return any(haystack[start : start + size] == needle for start in range(len(haystack) - size + 1))


@dataclass(frozen=True)
class ScanWindow:
    """The newest messages of a conversation, read once for every entry. Each message is kept as written and as a stem
    sequence, so a key never matches across the end of one message and the start of the next."""

    texts: tuple[str, ...]
    stem_sequences: tuple[tuple[str, ...], ...]
    language: str

    @classmethod
    def of(cls, messages: Sequence[str], scan_depth: int, language: str | None) -> "ScanWindow":
        """Returns the window of the newest `scan_depth` of `messages`, which are listed oldest first and come from both
        sides, the current message included. A depth under 1 gives an empty window. `language` stems the messages."""
        newest = tuple(messages[len(messages) - scan_depth :]) if scan_depth > 0 else ()
        return cls(
            texts=newest,
            stem_sequences=tuple(tuple(stems(text, language)) for text in newest),
            language=language_code(language) or "",
        )

    def has_stems(self, sequence: Sequence[str]) -> bool:
        needle = tuple(sequence)
        return any(_contains_sequence(message, needle) for message in self.stem_sequences)

    def has_exact(self, key: str) -> bool:
        return any(_contains_exact(text, key) for text in self.texts)


def _first_matching(
    window: ScanWindow, keys: Sequence[str], sequences: Sequence[Sequence[str]], case_sensitive: bool
) -> str | None:
    """Returns the first key of the list that the window matches, or None. A case-sensitive entry compares the key text;
    any other compares stems. `sequences` holds one stem sequence per key in the same order."""
    for key, sequence in zip(keys, sequences, strict=True):
        if window.has_exact(key) if case_sensitive else window.has_stems(sequence):
            return key
    return None


def _sequences_for(entry: LoreEntry, name: str, window: ScanWindow) -> tuple[Sequence[str], ...]:
    """Returns the stem sequence of each key of the list `name`. The stems saved with the entry are used when they were
    made for the window's language; otherwise the key text is stemmed again, so a key reads the same way as the text it
    is compared with. A key with no stem in that language gets an empty sequence, which never matches."""
    keys: tuple[str, ...] = getattr(entry, name)
    if entry.stems.language == window.language and len(getattr(entry.stems, name)) == len(keys):
        return tuple(getattr(entry.stems, name))
    return tuple(tuple(stems(key, window.language)) for key in keys)


def match_entry(entry: LoreEntry, window: ScanWindow) -> EntryMatch:
    """Returns whether `entry` is active in `window`, and why.

    - A disabled entry is never active, even a constant one: `enabled` is the switch that turns an entry off.
    - A constant entry is active without any key, and its exclude keys do not apply.
    - Otherwise some key must match; when the entry has secondary keys one of them must match too; and no exclude key
      may match. The exclude keys decide last, so the answer names the key that excluded the entry.
    """
    if not entry.enabled:
        return EntryMatch(entry.id, False, MatchReason.DISABLED)
    if entry.constant:
        return EntryMatch(entry.id, True, MatchReason.CONSTANT)
    if not entry.keys:
        return EntryMatch(entry.id, False, MatchReason.NO_KEYS)
    exact = entry.case_sensitive
    key = _first_matching(window, entry.keys, _sequences_for(entry, "keys", window), exact)
    if key is None:
        return EntryMatch(entry.id, False, MatchReason.NO_KEY_MATCHED)
    secondary = None
    if entry.secondary_keys:
        sequences = _sequences_for(entry, "secondary_keys", window)
        secondary = _first_matching(window, entry.secondary_keys, sequences, exact)
        if secondary is None:
            return EntryMatch(entry.id, False, MatchReason.SECONDARY_NOT_MATCHED, matched_key=key)
    excluded = _first_matching(window, entry.exclude_keys, _sequences_for(entry, "exclude_keys", window), exact)
    if excluded is not None:
        return EntryMatch(
            entry.id,
            False,
            MatchReason.EXCLUDED,
            matched_key=key,
            matched_secondary_key=secondary,
            excluded_key=excluded,
        )
    return EntryMatch(entry.id, True, MatchReason.MATCHED, matched_key=key, matched_secondary_key=secondary)


def match_entries(
    entries: Sequence[LoreEntry], messages: Sequence[str], scan_depth: int, language: str | None
) -> list[EntryMatch]:
    """Returns one outcome per entry, in the order of `entries`, for the newest `scan_depth` of `messages` (oldest
    first, both sides, the current message last). `language` is the conversation's: it stems the messages, and any key
    saved in another language is stemmed again. Never raises for text, and runs in time proportional to the window's
    words times the keys, so it is safe on pasted text."""
    window = ScanWindow.of(messages, scan_depth, language)
    return [match_entry(entry, window) for entry in entries]
