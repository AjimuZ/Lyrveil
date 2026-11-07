"""Reply checks V1 to V13 and the adaptive notes of the anchor (docs/consistency-engine.md, Reply checks; ADR 0032):
deterministic checks over the text a model writes for a character.

The checks read the card, the user and the chat's language (`CheckRules`), and the lists under `prompts/<language>/`.
While a reply streams, `TurnCutoff` runs before the reply normalizer and ends the reply at control text of the
template family, a frame marker of the dynamic context or the user's turn (V2, V3). `SentenceSplitter` cuts the text
the normalizer releases into sentences, and `sentence_checks` runs the blocking check V4 and the corrected checks V6,
V10 and V11 on each one, so the caller can stop the generation. `check_reply` counts every check on the delivered
text for `checks_json`: the sentence checks again, then V5, V7, V8, V9, V12 and V13. V1, an empty reply, is the
caller's, because the caller decides the retry. `active_notes` and `note_text` turn the checks recorded on the last
replies into the anchor's corrective notes.

Text is compared folded: lowercase and without accents, one character for each character of the text, so a match in
the folded text has the same place in the text as written. Text inside quotation marks is masked for the checks of
first- and second-person speech (V6, V10 to V13), because it is reported speech. Every check reads its input one
character at a time or once whole, so no verdict depends on how a reply was split into chunks.
"""

import difflib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from lyrveil.consistency.content_guard import find_reply_violation
from lyrveil.context.minimal import LANGUAGE_NAMES, UserProfile
from lyrveil.domain.card import CharacterCard
from lyrveil.memory.text import stop_words, supported_languages

# The lists, one folder per language code.
LIST_DIR = Path(__file__).resolve().parents[1] / "prompts"
# The anchor's notes are Italian, like every block of the reply prompt (lyrveil.context.blocks).
NOTE_LANGUAGE = "it"

# docs/consistency-engine.md (Reply checks): V4 always stops a generation; V6, V10 and V11 stop the first one and start
# the corrective generation, which only V4 stops.
BLOCKING = frozenset({"V4"})
CORRECTED = frozenset({"V6", "V10", "V11"})
STOPPING = BLOCKING | CORRECTED
# Adaptive notes: each lasts this many replies, and at most this many are active (ADR 0032).
NOTE_REPLIES = 3
MAX_ACTIVE_NOTES = 2
# V7 compares a reply with this many previous replies, by their first words or by their similarity.
PREVIOUS_REPLIES = 10
REPEAT_WORDS = 3
REPEAT_SIMILARITY = 0.9
# V8 reads only replies of at least this many words, since a short reply has too few stop words to tell a language.
LANGUAGE_MIN_WORDS = 8

# Stands for a character inside quotation marks in Sentence.masked.
MASK = "\x01"
# The two frame markers of the dynamic context (lyrveil.prompts.formatter), as a model may copy them.
FRAME_MARKERS = ("[Contesto per", "[contesto per", "[Messaggio di", "[messaggio di")

# Named escapes keep look-alike characters readable in the source.
_APOSTROPHES = frozenset("'\N{RIGHT SINGLE QUOTATION MARK}\N{LEFT SINGLE QUOTATION MARK}")
_LEFT_QUOTE = "\N{LEFT DOUBLE QUOTATION MARK}"
# Opening quotes and the closing quote of each; the straight quote closes itself.
_QUOTES = {
    '"': '"',
    _LEFT_QUOTE: "\N{RIGHT DOUBLE QUOTATION MARK}",
    "«": "»",
    "\N{DOUBLE LOW-9 QUOTATION MARK}": _LEFT_QUOTE,
}
_SENTENCE_ENDS = frozenset(".!?…")
# What may follow the end punctuation of a sentence before the whitespace that ends it.
_AFTER_END = frozenset("\"»)]*'" + "\N{RIGHT DOUBLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}")

_WORD = re.compile(r"[^\W_]+")
_PLACEHOLDERS = re.compile(r"(\{n\}|\{end\})")
_NUMBER = r"(?P<n>[0-9]{1,3}|[^\W\d_]+)"
_CLAUSE_END = r"(?=\s*(?:[,.;:!?…]|$))"
_NAME_AFTER = re.compile(r"\s+([^\W\d_]+)")
_NEXT_WORD = re.compile(r"\s*([^\W_]+)")
_LAST_WORD = re.compile(r"([^\W_]+)\s*$")
# A phrase of V6 right after one of these words is denied, not stated.
_NEGATIONS = frozenset({"non", "not", "nicht", "no", "ne"})
# The ending that contradicts each grammatical gender in V11 and V12.
_WRONG_ENDING = {"femminile": "o", "maschile": "a"}


def _italian_numbers() -> dict[str, int]:
    """Returns the Italian words for 0 to 99 and 100, folded: "trentun" and "trentuno" are 31, "ventitre" is 23."""
    units = [
        "zero",
        "uno",
        "due",
        "tre",
        "quattro",
        "cinque",
        "sei",
        "sette",
        "otto",
        "nove",
        "dieci",
        "undici",
        "dodici",
        "tredici",
        "quattordici",
        "quindici",
        "sedici",
        "diciassette",
        "diciotto",
        "diciannove",
    ]
    tens = ["venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta"]
    numbers = {word: value for value, word in enumerate(units)}
    numbers["un"] = 1
    for index, ten in enumerate(tens):
        base = 20 + 10 * index
        numbers[ten] = base
        for unit in range(1, 10):
            # The tens lose their last vowel before "uno" and "otto": "ventuno", "trentotto".
            stem = ten[:-1] if unit in (1, 8) else ten
            numbers[stem + units[unit]] = base + unit
        numbers[ten[:-1] + "un"] = base + 1
    numbers["cento"] = 100
    return numbers


_ITALIAN_NUMBERS = _italian_numbers()


def _fold_char(char: str) -> str:
    if char in _APOSTROPHES:
        return "'"
    return unicodedata.normalize("NFKD", char.lower())[:1] or char


def fold(text: str) -> str:
    """Returns `text` lowercase and without accents, with one character for each character of `text`, so indices agree.
    Every apostrophe becomes "'"."""
    return "".join(_fold_char(char) for char in text)


def _words(text: str) -> list[str]:
    return _WORD.findall(fold(text))


@cache
def _sections(language: str, file_name: str) -> dict[str, tuple[str, ...]]:
    """Returns the entries of the list `prompts/<language>/<file_name>` by section, in file order; {} when the language
    has no such list. A line `[name]` starts a section, entries before the first one belong to section "", and blank
    lines and `#` comments are ignored. The result is shared: never change it."""
    path = LIST_DIR / language / file_name
    if language not in LANGUAGE_NAMES or not path.is_file():
        return {}
    sections: dict[str, list[str]] = {}
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry.startswith("[") and entry.endswith("]"):
            current = entry[1:-1]
            sections.setdefault(current, [])
        elif entry:
            sections.setdefault(current, []).append(entry)
    return {name: tuple(entries) for name, entries in sections.items()}


def _words_pattern(text: str) -> str:
    """Returns the regex of the folded words of `text`, separated by any whitespace."""
    return r"\s+".join(re.escape(word) for word in fold(text).split())


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Compiles a list phrase for folded text. It must start and end at word boundaries; `{n}` stands for a number in
    digits or a word (group `n`), and `{end}` for the end of a clause: punctuation or the end of the text."""
    parts: list[str] = []
    for part in _PLACEHOLDERS.split(phrase.strip()):
        if part == "{n}":
            parts.append(_NUMBER)
        elif part == "{end}":
            parts.append(_CLAUSE_END)
        elif part.strip():
            lead = r"\s+" if part[0].isspace() and parts else ""
            tail = r"\s+" if part[-1].isspace() else ""
            parts.append(lead + _words_pattern(part) + tail)
        elif part and parts:
            parts.append(r"\s+")
    return re.compile(r"(?<![^\W_])" + "".join(parts) + r"(?![^\W_])")


def _alternation(entries: Iterable[str]) -> str:
    """Returns one regex group for `entries`, longest first, so a longer phrase wins at the same place."""
    patterns = {_words_pattern(entry) for entry in entries if entry.strip()}
    return "(?:" + "|".join(sorted(patterns, key=lambda pattern: (-len(pattern), pattern))) + ")"


@dataclass(frozen=True)
class _PhraseList:
    """Compiled phrases of one list, with the folded words that rule a match out."""

    phrases: tuple[re.Pattern[str], ...]
    excluded: frozenset[str]


@cache
def _phrase_list(language: str, file_name: str, excluded: str) -> _PhraseList:
    sections = _sections(language, file_name)
    phrases = tuple(_phrase_pattern(phrase) for phrase in sections.get("phrases", ()))
    return _PhraseList(phrases, frozenset(fold(word) for word in sections.get(excluded, ())))


@cache
def _agreement(person: str) -> re.Pattern[str] | None:
    """Compiles the Italian agreement pattern of `person`, `first_person` (V11) or `second_person` (V12); group
    `ending` is the gendered ending. None without the list."""
    sections = _sections("it", "gender_agreement.txt")
    leads, stems, adverbs = sections.get(person, ()), sections.get("stems", ()), sections.get("adverbs", ())
    if not leads or not stems:
        return None
    adverb = rf"(?:\s+{_alternation(adverbs)})?" if adverbs else ""
    return re.compile(rf"(?<![^\W_]){_alternation(leads)}{adverb}\s+{_alternation(stems)}(?P<ending>[oa])(?![^\W_])")


@dataclass(frozen=True)
class _Forms:
    """The Italian forms of one address form: `written` as written and only inside a sentence, `any_case` on the
    lowercase text, accents kept."""

    written: tuple[re.Pattern[str], ...]
    any_case: re.Pattern[str] | None


def _exact(entry: str) -> str:
    return r"\s+".join(re.escape(word) for word in entry.split())


@cache
def _address_forms(form: str) -> _Forms:
    """Returns the forms of the address form `form`, `lei` or `tu`, from `prompts/it/address_forms.txt`."""
    entries = _sections("it", "address_forms.txt").get(form, ())
    written = tuple(re.compile(rf"(?<![^\W_]){_exact(entry)}(?![^\W_])") for entry in entries if entry[:1].isupper())
    lower = sorted({_exact(entry.lower()) for entry in entries if not entry[:1].isupper()}, key=lambda p: (-len(p), p))
    any_case = re.compile(r"(?<![^\W_])(?:" + "|".join(lower) + r")(?![^\W_])") if lower else None
    return _Forms(written, any_case)


@cache
def _model_talk(language: str) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    """Returns the V5 phrases of `language`: those about being a model, then those that show the user asked."""
    sections = _sections(language, "model_talk.txt")
    talk = tuple(_phrase_pattern(phrase) for phrase in sections.get("talk", ()))
    return talk, tuple(_phrase_pattern(phrase) for phrase in sections.get("asked", ()))


@dataclass(frozen=True)
class _Calque:
    """One V9 phrase: `opening` counts only at the start of the reply, `repeated` only after an earlier use."""

    pattern: re.Pattern[str]
    opening: bool
    repeated: bool


@cache
def _calques(language: str) -> tuple[_Calque, ...]:
    entries = _sections(language, "calques.txt").get("phrases", ())
    return tuple(
        _Calque(_phrase_pattern(entry.lstrip("^+")), entry.startswith("^"), entry.startswith("+")) for entry in entries
    )


@cache
def _note_templates() -> dict[str, str]:
    """Returns the note template of each check, from `prompts/it/notes.txt`."""
    templates: dict[str, str] = {}
    for entry in _sections(NOTE_LANGUAGE, "notes.txt").get("", ()):
        check, _, template = entry.partition(":")
        templates[check.strip()] = template.strip()
    return templates


@dataclass(frozen=True)
class CheckRules:
    """What the checks read from the chat: the folded words of the character's full name, its age, its
    `grammatical_gender` and `address_form`, the user's `grammatical_gender`, and the chat's language code."""

    names: frozenset[str]
    age: int
    gender: str
    address_form: str
    user_gender: str
    language: str

    @classmethod
    def for_chat(cls, card: CharacterCard, user: UserProfile, language: str) -> "CheckRules":
        """Returns the rules of a chat of `card` with `user` in `language`; a code without lists reads as Italian."""
        identity = card.identity
        names = frozenset(_words(f"{identity.first_name} {identity.last_name or ''}"))
        code = language if language in LANGUAGE_NAMES else "it"
        return cls(
            names,
            identity.age,
            identity.grammatical_gender,
            card.communication.address_form,
            user.grammatical_gender,
            code,
        )


@dataclass(frozen=True)
class Sentence:
    """One sentence of a reply: `text` as written, and `masked`, the same text with every quotation, its quotes
    included, replaced by MASK, so that the two align character by character."""

    text: str
    masked: str


class SentenceSplitter:
    """Cuts streamed text into sentences: feed it the text in order, then call `finish` once.

    A sentence ends at a line break, or at whitespace after `.`, `!`, `?` or `…` and any closing quotes, brackets or
    asterisks. A quotation runs from an opening quote to its closing quote, and a line break ends it, so an unpaired
    quote never masks more than its line. Sentences are returned without their surrounding whitespace, and a blank
    one is dropped. The result never depends on how the text was split.
    """

    def __init__(self) -> None:
        self._text: list[str] = []
        self._masked: list[str] = []
        self._closer: str | None = None  # The quote that closes the open quotation.
        self._ending = False  # Whether whitespace now would end the sentence.

    def feed(self, text: str) -> list[Sentence]:
        """Takes the next piece of text and returns the sentences it completes."""
        out: list[Sentence] = []
        for char in text:
            if char == "\n":
                self._emit(out)
                self._closer = None
            elif char.isspace() and self._ending:
                self._emit(out)
            else:
                self._text.append(char)
                self._masked.append(self._mask(char))
                if char in _SENTENCE_ENDS:
                    self._ending = True
                elif not (self._ending and char in _AFTER_END):
                    self._ending = False
        return out

    def finish(self) -> list[Sentence]:
        """Returns the last sentence, if any text is left."""
        out: list[Sentence] = []
        self._emit(out)
        return out

    def _mask(self, char: str) -> str:
        if self._closer is not None:
            if char == self._closer:
                self._closer = None
            return MASK
        closer = _QUOTES.get(char)
        if closer is None:
            return char
        self._closer = closer
        return MASK

    def _emit(self, out: list[Sentence]) -> None:
        text, masked = "".join(self._text), "".join(self._masked)
        self._text, self._masked, self._ending = [], [], False
        start, end = len(text) - len(text.lstrip()), len(text.rstrip())
        if start < end:
            out.append(Sentence(text[start:end], masked[start:end]))


def split_sentences(text: str) -> list[Sentence]:
    """Returns the sentences of a text known in one piece, as a SentenceSplitter gives them."""
    splitter = SentenceSplitter()
    return splitter.feed(text) + splitter.finish()


def _number(word: str, language: str) -> int | None:
    if word.isdigit():
        return int(word)
    return _ITALIAN_NUMBERS.get(word) if language == "it" else None


def _wrong_names(masked: str, folded: str, rules: CheckRules) -> int:
    """V6: counts the phrases of `self_name.txt` followed by a capitalized word that is not one of the character's
    names or of the list's `not_names`, and that no negation comes before."""
    names = _phrase_list(rules.language, "self_name.txt", "not_names")
    hits = 0
    for pattern in names.phrases:
        for match in pattern.finditer(folded):
            before = _LAST_WORD.search(folded, 0, match.start())
            if before is not None and before.group(1) in _NEGATIONS:
                continue
            name = _NAME_AFTER.match(masked, match.end())
            if name is None or not name.group(1)[0].isupper():
                continue
            word = fold(name.group(1))
            if word not in rules.names and word not in names.excluded:
                hits += 1
    return hits


def _wrong_ages(folded: str, rules: CheckRules) -> int:
    """V10: counts the phrases of `self_age.txt` that state an age other than the card's, unless a word of the list's
    `not_before` follows."""
    ages = _phrase_list(rules.language, "self_age.txt", "not_before")
    hits = 0
    for pattern in ages.phrases:
        for match in pattern.finditer(folded):
            stated = match.groupdict().get("n")
            value = None if stated is None else _number(stated, rules.language)
            if value is None or value == rules.age:
                continue
            after = _NEXT_WORD.match(folded, match.end())
            if after is None or after.group(1) not in ages.excluded:
                hits += 1
    return hits


def _agreement_hits(folded: str, gender: str, person: str) -> int:
    """V11 (`first_person`) and V12 (`second_person`): counts the agreements whose ending contradicts `gender`. Zero for
    `non_indicato`."""
    wrong, pattern = _WRONG_ENDING.get(gender), _agreement(person)
    if wrong is None or pattern is None:
        return 0
    return sum(1 for match in pattern.finditer(folded) if match.group("ending") == wrong)


def _inside(text: str, start: int) -> bool:
    """Whether `start` is inside a sentence: after a word or a comma, not at its start or after a colon."""
    before = text[:start].rstrip()
    return bool(before) and (before[-1].isalnum() or before[-1] in ",;")


def _address_hits(masked: str, address_form: str) -> int:
    """V13: counts the forms of the other address form in a masked Italian sentence."""
    forms = _address_forms("lei" if address_form == "tu" else "tu")
    hits = sum(1 for pattern in forms.written for match in pattern.finditer(masked) if _inside(masked, match.start()))
    if forms.any_case is not None:
        hits += sum(1 for _ in forms.any_case.finditer(masked.lower()))
    return hits


def sentence_checks(sentence: Sentence, rules: CheckRules) -> list[str]:
    """Returns the blocking and corrected checks that `sentence` breaks, one entry per hit, in the order V4, V6, V10,
    V11. V4 reads the sentence as written, quotations included; the others skip quotations. V11 runs for Italian
    only."""
    hits: list[str] = []
    if find_reply_violation(sentence.text) is not None:
        hits.append("V4")
    folded = fold(sentence.masked)
    hits.extend(["V6"] * _wrong_names(sentence.masked, folded, rules))
    hits.extend(["V10"] * _wrong_ages(folded, rules))
    if rules.language == "it":
        hits.extend(["V11"] * _agreement_hits(folded, rules.gender, "first_person"))
    return hits


def first_stop(sentences: Iterable[Sentence], rules: CheckRules, stop_on: frozenset[str]) -> str | None:
    """Returns the first check of `stop_on` that one of `sentences` breaks, in sentence order, or None."""
    for sentence in sentences:
        for hit in sentence_checks(sentence, rules):
            if hit in stop_on:
                return hit
    return None


def _model_talk_hits(text: str, user_message: str, language: str) -> int:
    """V5: counts the phrases about being a model in `text`, or 0 when `user_message` shows the user asked."""
    talk, asked = _model_talk(language)
    folded = fold(text)
    hits = sum(1 for pattern in talk for _ in pattern.finditer(folded))
    question = fold(user_message)
    if hits and any(pattern.search(question) for pattern in (*talk, *asked)):
        return 0
    return hits


def _repeats(text: str, previous: Sequence[str]) -> int:
    """V7: 1 when `text` starts with the same REPEAT_WORDS words as one of `previous`, or its words are at least
    REPEAT_SIMILARITY similar to one of them (difflib's ratio over the folded words), else 0."""
    words = _words(text)
    if not words:
        return 0
    for earlier in previous:
        other = _words(earlier)
        if not other:
            continue
        if len(words) >= REPEAT_WORDS and words[:REPEAT_WORDS] == other[:REPEAT_WORDS]:
            return 1
        if difflib.SequenceMatcher(None, words, other, autojunk=False).ratio() >= REPEAT_SIMILARITY:
            return 1
    return 0


def _other_language(text: str, expected: str) -> int:
    """V8: 1 when more words of `text` are stop words of another supported language than of `expected`, else 0. A
    reply under LANGUAGE_MIN_WORDS words, or a language without a stop-word list, gives 0."""
    languages = supported_languages()
    words = _words(text)
    if expected not in languages or len(words) < LANGUAGE_MIN_WORDS:
        return 0
    counts = {code: sum(1 for word in words if word in stop_words(code)) for code in languages}
    own = counts.pop(expected)
    return int(max(counts.values()) > own)


def _calque_hits(text: str, previous: Sequence[str], language: str) -> int:
    """V9: counts the phrases of `calques.txt` in `text`, with the list's opening and repetition rules."""
    folded = fold(text)
    hits = 0
    for calque in _calques(language):
        if calque.opening:
            found = int(calque.pattern.match(folded) is not None)
        else:
            found = sum(1 for _ in calque.pattern.finditer(folded))
        if found and calque.repeated and not any(calque.pattern.search(fold(earlier)) for earlier in previous):
            found = 0
        hits += found
    return hits


def check_reply(
    text: str, rules: CheckRules, *, previous: Sequence[str] = (), user_message: str = ""
) -> dict[str, int]:
    """Returns the hits of every check on a delivered reply, `{check: count}` in check order, with only the checks
    that fired: V4, V6, V10 and V11 over its sentences, V12 and V13 over its sentences for Italian, V5 unless
    `user_message` asked about it, V7 and the repetition rule of V9 against the newest PREVIOUS_REPLIES of `previous`
    (the character's earlier replies, oldest first), V8 against the chat's language and V9. An empty text gives {}.
    """
    counts: Counter[str] = Counter()
    for sentence in split_sentences(text):
        counts.update(sentence_checks(sentence, rules))
        if rules.language == "it":
            counts["V12"] += _agreement_hits(fold(sentence.masked), rules.user_gender, "second_person")
            counts["V13"] += _address_hits(sentence.masked, rules.address_form)
    if text.strip():
        recent = previous[-PREVIOUS_REPLIES:]
        counts["V5"] += _model_talk_hits(text, user_message, rules.language)
        counts["V7"] += _repeats(text, recent)
        counts["V8"] += _other_language(text, rules.language)
        counts["V9"] += _calque_hits(text, recent, rules.language)
    return {check: count for check, count in sorted(counts.items(), key=lambda item: int(item[0][1:])) if count}


class TurnCutoff:
    """V2 and V3 while a reply streams: ends the reply where control text of the template family, a frame marker of
    the dynamic context (V2), or a line that opens with the user's name and ":" (V3) begins.

    Feed it the runtime's text in order, before the normalizer, then call `finish` once. It holds back only the end
    of the text while it may still start a marker. Once a marker appears, the text before it is released, `cut` names
    the check, and everything after it is dropped. A reply that opens with the user's name and ":" is cut at once, so
    it is empty, which the caller treats as V1. The released text never depends on how the input was split.
    """

    def __init__(self, control_texts: Sequence[str], user_name: str) -> None:
        markers = dict.fromkeys((*control_texts, *FRAME_MARKERS), "V2")
        if user_name:
            markers[f"\n{user_name}:"] = "V3"
        self._markers = {marker: check for marker, check in markers.items() if marker}
        self._longest = max((len(marker) for marker in self._markers), default=0)
        # A line break stands before the reply, so that its first line is a line too; it is never released.
        self._held = "\n"
        self._virtual = True
        self.cut: str | None = None

    def feed(self, text: str) -> str:
        """Takes the next piece of raw text and returns the text it releases; "" after a cut."""
        if self.cut is not None:
            return ""
        buffer = self._held + text
        first: tuple[int, str] | None = None
        for marker, check in self._markers.items():
            index = buffer.find(marker)
            if index >= 0 and (first is None or index < first[0]):
                first = (index, check)
        if first is not None:
            self.cut = first[1]
            self._held = ""
            return self._release(buffer[: first[0]])
        keep = self._partial(buffer)
        self._held = buffer[len(buffer) - keep :]
        return self._release(buffer[: len(buffer) - keep])

    def finish(self) -> str:
        """Returns the text still held back: the input ended, so it starts no marker."""
        if self.cut is not None:
            return ""
        held, self._held = self._held, ""
        return self._release(held)

    def _partial(self, buffer: str) -> int:
        """Returns the length of the longest end of `buffer` that starts a marker without completing it."""
        for size in range(min(len(buffer), self._longest - 1), 0, -1):
            tail = buffer[-size:]
            if any(marker.startswith(tail) for marker in self._markers):
                return size
        return 0

    def _release(self, text: str) -> str:
        if self._virtual and text:
            self._virtual = False
            return text[1:]
        return text


def recorded_checks(checks_json: str | None) -> dict[str, int]:
    """Returns the `checks` part of a variant's `checks_json`, {} when the variant has none or it cannot be read."""
    if not checks_json:
        return {}
    try:
        value = json.loads(checks_json)
    except ValueError:
        return {}
    checks = value.get("checks") if isinstance(value, dict) else None
    if not isinstance(checks, dict):
        return {}
    return {
        str(check): count
        for check, count in checks.items()
        if isinstance(count, int) and not isinstance(count, bool) and count > 0
    }


def active_notes(replies: Sequence[Mapping[str, int]]) -> tuple[str, ...]:
    """Returns the checks whose adaptive note the next reply's anchor holds, oldest first, from the recorded checks of
    the chat's previous replies, oldest first (ADR 0032).

    A reply starts the note of V8, V9 or V13 when it hit that check, and the note of V7 when it and the reply before it
    both hit V7. A note lasts the NOTE_REPLIES replies after the one that started it, and starting it again restarts
    it. At most MAX_ACTIVE_NOTES are active: a newer note replaces the oldest one, and notes started by the same reply
    are taken in the order V7, V8, V9, V13. Only the newest NOTE_REPLIES replies and the one before them matter.
    """
    started: list[tuple[int, str]] = []
    for index, checks in enumerate(replies):
        if checks.get("V7") and index > 0 and replies[index - 1].get("V7"):
            started.append((index, "V7"))
        started.extend((index, check) for check in ("V8", "V9", "V13") if checks.get(check))
    active: list[str] = []
    for index, check in started:
        if len(replies) - index <= NOTE_REPLIES:
            if check in active:
                active.remove(check)
            active.append(check)
    return tuple(active[-MAX_ACTIVE_NOTES:])


def note_text(check: str, card: CharacterCard, user: UserProfile, language: str) -> str:
    """Returns the corrective note of `check` (`prompts/it/notes.txt`) for a chat of `card` with `user` in `language`.
    Raises KeyError for a check without a note: only V6, V7, V8, V9, V10, V11 and V13 have one."""
    identity = card.identity
    return _note_templates()[check].format_map(
        {
            "first_name": identity.first_name,
            "age": identity.age,
            "gender": identity.grammatical_gender,
            "address_form": card.communication.address_form,
            "user_name": user.display_name,
            "language_name": LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["it"]),
        }
    )
