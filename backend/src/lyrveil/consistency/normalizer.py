"""ReplyNormalizer (docs/consistency-engine.md, Reply normalization; ADR 0032): rules N1 to N5, which give a reply the
same surface form whatever profile wrote it.

The rules are deterministic, read only the card's names, `narration` and `emoji`, and only remove or reshape text: the
one text they add is the `…` of N4. The normalizer runs on the streamed text. `feed` returns at once what no later
character can change, and holds back only a possible speaker label or wrapping quote at the start, the start of a line
that may still be Markdown, an asterisk span that may be removed, and whitespace that may turn out to be trailing.
Every stage reads its input one character at a time, or decides only on facts that more text cannot change, so the
delivered text is the same however the reply is split into chunks (T-CON-09).

The stages run in this order: N1, N2, N3, the emoji half of N5, then the spacing half of N5, which also closes the gap
that removed text leaves. N4 needs the whole text, so it runs last, in `finish`.
"""

import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from lyrveil.domain.card import CharacterCard, EmojiUse, Narration
from lyrveil.ports.text_runtime import FinishReason

# N1 treats quotes as wrapping the reply when no closing quote falls within the first this many characters of the
# reply, after its leading whitespace, or when the closing quote there ends the reply (ADR 0032: the 40 characters
# held back until N1 decides).
QUOTE_WINDOW = 40
ELLIPSIS = "…"

# Named escapes keep look-alike and invisible characters readable in the source.
_LEFT_QUOTE = "\N{LEFT DOUBLE QUOTATION MARK}"
_RIGHT_QUOTE = "\N{RIGHT DOUBLE QUOTATION MARK}"
_APOSTROPHE = "\N{RIGHT SINGLE QUOTATION MARK}"
_DASHES = "\N{EN DASH}\N{EM DASH}"
# Opening quotes and the closing quote of each; the straight quote closes itself.
_QUOTES = {'"': '"', _LEFT_QUOTE: _RIGHT_QUOTE, "«": "»", "\N{DOUBLE LOW-9 QUOTATION MARK}": _LEFT_QUOTE}
# The separators of a speaker label besides "-", which also needs whitespace after it.
_LABEL_SEPARATORS = frozenset(":" + _DASHES)
# Stands for removed text between stages. `feed` drops it from the input, so only a stage can write it.
_REMOVED = "\x00"
_SPACES = " \t"
# Removed text never leaves a space before these.
_CLOSING_PUNCTUATION = frozenset(",.;:!?…)]}»" + _RIGHT_QUOTE + _APOSTROPHE)

# N2: a line start that may still become a rule, a whole rule line, heading marks, an empty heading, and a table
# separator row, which must start with "|".
_RULE_PREFIX = re.compile(r"([-*_])(?:[ \t]*\1)*[ \t]*")
_RULE = re.compile(r"[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*")
_HEADING_MARKS = re.compile(r"#{1,6}")
_EMPTY_HEADING = re.compile(r"[ \t]*#{1,6}[ \t]*")
_TABLE_SEPARATOR = re.compile(r"[ \t]*\|(?:[ \t]*:?-+:?[ \t]*\|)*[ \t]*:?-+:?[ \t]*\|?[ \t]*")
_FENCES = ("```", "~~~")

# N4: a sentence end, with the closing quotes, brackets or asterisks that follow it, before whitespace or the end.
_SENTENCE_END = re.compile(r"[.!?…]+[" + re.escape('"»)]*' + _RIGHT_QUOTE + _APOSTROPHE) + r"]*(?=\s|$)")
# N4 drops these from the end of a cut reply before the ellipsis.
_TRAILING_BEFORE_ELLIPSIS = " \t\n,;:-" + _DASHES

# N5: code points read as emoji. They cover the pictographs, emoticons, flags and skin tones above U+1F000, the
# symbols and dingbats from U+2600 to U+27BF, and the few emoji of other blocks, such as a watch, a star and a circle.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x231A, 0x231B),
    (0x23E9, 0x23F3),
    (0x23F8, 0x23FA),
    (0x2B05, 0x2B07),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
)
_ZERO_WIDTH_JOINER = "\N{ZERO WIDTH JOINER}"
# The emoji presentation selector and the keycap mark only shape an emoji, so they go wherever they are.
_EMOJI_MARKS = frozenset({"\N{VARIATION SELECTOR-16}", "\N{COMBINING ENCLOSING KEYCAP}"})
_TAGS = (0xE0020, 0xE007F)  # The tag characters of subdivision flags.


@dataclass(frozen=True)
class NormalizerRules:
    """What the rules read from the card: the names a label may use, longest first, `narration` and `emoji`."""

    names: tuple[str, ...]
    narration: Narration = "nessuna"
    emoji: EmojiUse = "raramente"

    @classmethod
    def for_card(cls, card: CharacterCard) -> "NormalizerRules":
        """Returns the rules of `card`: its full name and its first name, then its `narration` and `emoji`."""
        identity = card.identity
        first = identity.first_name
        names = (f"{first} {identity.last_name}", first) if identity.last_name else (first,)
        return cls(names, card.communication.narration, card.communication.emoji)


@dataclass(frozen=True)
class NormalizedReply:
    """A finished normalization.

    `text` is the delivered text. `tail` is what `finish` added after the text `feed` had released, or "" when the
    delivered text does not extend it, because N4 cut it. `fired` counts the changes of each rule that made one: N1
    the label and the wrapping quotes; N2 each heading, rule, table row, fence line and `**` marker; N3 each removed
    span; N4 the cut or the ellipsis; N5 each removed run of emoji and each run of blank lines it shortened.
    """

    text: str
    tail: str
    fired: dict[str, int] = field(default_factory=dict)

    def checks(self) -> dict[str, dict[str, int]]:
        """Returns the normalizer's part of `message_variants.checks_json`."""
        return {"normalizer": dict(self.fired)}


class _Stage(ABC):
    """One step of the pipeline. `feed` returns the text it releases; `finish` returns the rest once the input ended.

    For any split of the same input into `feed` calls, the released texts joined with the `finish` text are the same.
    """

    @abstractmethod
    def feed(self, text: str) -> str:
        """Takes the next piece of the input and returns the text released by it."""

    def finish(self) -> str:
        return ""


def _match_label(text: str, name: str, *, final: bool) -> int | None:
    """Returns the length of a speaker label of `name` at the start of `text`, 0 when `text` does not start with one,
    or None while more text could still make one. `final` means no more text comes.

    A label is the name in any case, with up to two asterisks around it, optional spaces, then ":", an en or em dash,
    or "-" followed by whitespace. Asterisks opened before the name and not closed before the separator are taken
    after it, so `**Nerea:**` is one label.
    """
    undecided = None if not final else 0
    size, index = len(text), 0
    lead = 0
    while index < size and text[index] == "*" and lead < 2:
        index, lead = index + 1, lead + 1
    part = text[index : index + len(name)]
    if part.lower() != name[: len(part)].lower():
        return 0
    if len(part) < len(name):
        return undecided
    index += len(name)
    middle = 0
    while index < size and text[index] == "*" and middle < 2:
        index, middle = index + 1, middle + 1
    while index < size and text[index] in _SPACES:
        index += 1
    if index == size:
        return undecided
    if text[index] == "-":
        if index + 1 == size:
            return undecided
        if not text[index + 1].isspace():
            return 0
    elif text[index] not in _LABEL_SEPARATORS:
        return 0
    index += 1
    for _ in range(lead - middle):
        if index == size:
            return None if not final else index
        if text[index] != "*":
            break
        index += 1
    return index


def _label_length(text: str, names: tuple[str, ...], *, final: bool) -> int | None:
    """Returns the length of the leading speaker label of `text`, 0 when it has none, or None while undecided. The
    names are tried in order, and a later name counts only once every earlier one is ruled out."""
    for name in names:
        length = _match_label(text, name, final=final)
        if length != 0:
            return length
    return 0


class _Opening(_Stage):
    """N1: drops the reply's leading whitespace, a leading speaker label and the quotes that wrap the reply.

    After the label, a reply that starts with an opening quote is wrapped when the matching closing quote falls within
    QUOTE_WINDOW characters and only whitespace follows it, or when no closing quote falls within them. In the second
    case the first closing quote that follows, wherever it is, goes with the opening one, so the quotes left stay
    paired. A closing quote within the window followed by more text means a quoted line, which stays.
    """

    def __init__(self, names: tuple[str, ...], fired: Counter[str]) -> None:
        self._names = names
        self._fired = fired
        self._head = ""  # The reply after its leading whitespace, while N1 has not decided.
        self._deciding = True
        self._closer: str | None = None  # The closing quote still to drop, after a dropped opening one.

    def feed(self, text: str) -> str:
        if not self._deciding:
            return self._pass(text)
        self._head += text if self._head else text.lstrip()
        return self._decide(final=False)

    def finish(self) -> str:
        return self._decide(final=True) if self._deciding else ""

    def _pass(self, text: str) -> str:
        if self._closer is None:
            return text
        index = text.find(self._closer)
        if index < 0:
            return text
        self._closer = None
        return text[:index] + text[index + 1 :]

    def _settle(self, label: int, *, quoted: bool, released: str) -> str:
        self._deciding = False
        self._head = ""
        self._fired["N1"] += int(label > 0) + int(quoted)
        return released

    def _decide(self, *, final: bool) -> str:
        head = self._head
        label = _label_length(head, self._names, final=final)
        if label is None:
            return ""
        body_at = len(head) - len(head[label:].lstrip())
        body = head[body_at:]
        if not body:
            return self._settle(label, quoted=False, released="") if final else ""
        closer = _QUOTES.get(body[0])
        if closer is None:
            return self._settle(label, quoted=False, released=body)
        end = body.find(closer, 1)
        if end >= 0 and body_at + end < QUOTE_WINDOW:
            if body[end + 1 :].strip():
                return self._settle(label, quoted=False, released=body)
            return self._settle(label, quoted=True, released=body[1:end]) if final else ""
        if end < 0 and len(head) < QUOTE_WINDOW and not final:
            return ""
        released = self._settle(label, quoted=True, released="")
        self._closer = closer
        return released + self._pass(body[1:])


class _Markdown(_Stage):
    """N2: turns Markdown headings, rules, tables and code fences into plain text and removes `**` markers.

    A heading keeps its title. A rule line, a fence line and a table separator row are removed with their line. A
    table row becomes its non-empty cells joined by ", ". Lines inside a fence stay as written. In other text a run of
    asterisks keeps one asterisk when its length is odd and none when it is even, so `**bold**` loses its markers and
    `*emphasis*` is left to N3. A line start is held only while it may still be one of these constructs.
    """

    def __init__(self, fired: Counter[str]) -> None:
        self._fired = fired
        self._line = ""  # The held start of the current line, or the whole row of a table line.
        self._mode: Literal["start", "text", "drop", "table", "code"] = "start"
        self._in_fence = False
        self._stars = 0  # The asterisks of the current run, not yet released.

    def feed(self, text: str) -> str:
        out: list[str] = []
        for char in text:
            if char == "\n":
                out.append(self._end_line())
                out.append("\n")
            elif self._mode == "text":
                out.append(self._text(char))
            elif self._mode == "code":
                out.append(char)
            elif self._mode == "table":
                self._line += char
            elif self._mode == "start":
                self._line += char
                out.append(self._start())
        return "".join(out)

    def finish(self) -> str:
        return self._end_line()

    def _text(self, char: str) -> str:
        if char == "*":
            self._stars += 1
            return ""
        return self._release_stars() + char

    def _text_run(self, text: str) -> str:
        return "".join(self._text(char) for char in text)

    def _release_stars(self) -> str:
        stars, self._stars = self._stars, 0
        self._fired["N2"] += stars // 2
        return "*" * (stars % 2)

    def _as_text(self, text: str) -> str:
        self._mode = "text"
        self._line = ""
        return self._text_run(text)

    def _fence_line(self) -> str:
        self._fired["N2"] += 1
        self._in_fence = not self._in_fence
        self._mode = "drop"
        self._line = ""
        return ""

    def _start(self) -> str:
        line = self._line
        lead = line.lstrip(_SPACES)
        if not lead:
            return ""
        if lead.startswith(_FENCES):
            return self._fence_line()
        if any(fence.startswith(lead) for fence in _FENCES):
            return ""
        if self._in_fence:
            self._mode = "code"
            self._line = ""
            return line
        first = lead[0]
        if first == "#":
            marks = _HEADING_MARKS.match(lead)
            assert marks is not None
            rest = lead[marks.end() :]
            if not rest:
                return ""
            if rest[0] not in _SPACES:
                return self._as_text(line)
            title = rest.lstrip(_SPACES)
            if not title:
                return ""
            self._fired["N2"] += 1
            return self._as_text(title)
        if first in "-*_":
            return "" if _RULE_PREFIX.fullmatch(lead) else self._as_text(line)
        if first == "|":
            self._mode = "table"
            return ""
        return self._as_text(line)

    def _end_line(self) -> str:
        """Returns what the current line still releases before its line break, and starts a new line."""
        mode, line = self._mode, self._line
        self._mode = "start"
        self._line = ""
        if mode == "text":
            return self._release_stars()
        if mode == "code":
            return ""
        if mode == "drop":
            return _REMOVED
        if mode == "table":
            self._fired["N2"] += 1
            if _TABLE_SEPARATOR.fullmatch(line):
                return _REMOVED
            cells = (cell.strip() for cell in line.strip().strip("|").split("|"))
            row = ", ".join(cell for cell in cells if cell)
            return self._text_run(row) + self._release_stars() if row else _REMOVED
        # The line ended while its start was held.
        if self._in_fence:
            return line
        if _RULE.fullmatch(line) or _EMPTY_HEADING.fullmatch(line):
            self._fired["N2"] += 1
            return _REMOVED
        return self._text_run(line) + self._release_stars()


def _word_count(text: str) -> int:
    return sum(1 for word in text.split() if any(char.isalnum() for char in word))


class _Narration(_Stage):
    """N3: applies `narration` to asterisk spans, the text between two asterisks on one line.

    A span of three or more words is narration: `nessuna` removes every one, `leggera` keeps the first and removes the
    later ones, `libera` keeps all. Shorter spans are emphasis and always stay, and an asterisk without a closing one
    on its line stays as written. Only a span that may be removed is held back until it closes.
    """

    def __init__(self, narration: Narration, fired: Counter[str]) -> None:
        self._narration = narration
        self._fired = fired
        self._span: str | None = None  # The text after an opening asterisk, while the span is open.
        self._holding = False  # Whether the open span is held back.
        self._kept = False  # Whether a narration span was kept, which `leggera` allows once.

    def feed(self, text: str) -> str:
        out: list[str] = []
        for char in text:
            if self._span is None:
                if char == "*":
                    self._span = ""
                    self._holding = self._narration == "nessuna" or (self._narration == "leggera" and self._kept)
                    if self._holding:
                        continue
                out.append(char)
            elif char == "*":
                out.append(self._close())
            elif char == "\n":
                out.append(self._unclosed() + char)
            else:
                self._span += char
                if not self._holding:
                    out.append(char)
        return "".join(out)

    def finish(self) -> str:
        return "" if self._span is None else self._unclosed()

    def _close(self) -> str:
        span, self._span = self._span or "", None
        narration = _word_count(span) >= 3
        if not self._holding:
            self._kept = self._kept or narration
            return "*"
        if not narration:
            return f"*{span}*"
        self._fired["N3"] += 1
        return _REMOVED

    def _unclosed(self) -> str:
        span, self._span = self._span or "", None
        return f"*{span}" if self._holding else ""


def _is_emoji(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in _EMOJI_RANGES)


def _joins_emoji(char: str) -> bool:
    """Whether `char` only continues an emoji sequence: a zero-width joiner or a tag character."""
    return char == _ZERO_WIDTH_JOINER or _TAGS[0] <= ord(char) <= _TAGS[1]


class _Emoji(_Stage):
    """N5, first half, only for `emoji` `mai`: removes emoji, with the joiners, selectors and tags of their sequences.
    A run of emoji is one removal."""

    def __init__(self, fired: Counter[str]) -> None:
        self._fired = fired
        self._after_emoji = False

    def feed(self, text: str) -> str:
        out: list[str] = []
        for char in text:
            if _is_emoji(char):
                if not self._after_emoji:
                    self._fired["N5"] += 1
                    out.append(_REMOVED)
                self._after_emoji = True
            elif char in _EMOJI_MARKS or (self._after_emoji and _joins_emoji(char)):
                continue
            else:
                self._after_emoji = False
                out.append(char)
        return "".join(out)


class _Spacing(_Stage):
    """N5, second half: drops the reply's leading and trailing whitespace and the trailing spaces of each line, keeps
    at most one blank line in a row, and closes the gap that removed text leaves.

    Where text was removed inside a line, the spaces around it become one space, or none before closing punctuation
    or at the start of the line. A line that held only removed text goes with its line break.
    """

    def __init__(self, fired: Counter[str]) -> None:
        self._fired = fired
        self._started = False  # Whether visible text was released.
        self._spaces = ""  # Spaces and tabs since the last visible character on this line.
        self._breaks = 0  # Line breaks kept since the last visible character.
        self._removed = False  # Whether text was removed on this line since the last visible character.
        self._line_text = False  # Whether the current line has visible text.

    def feed(self, text: str) -> str:
        out: list[str] = []
        for char in text:
            if char == _REMOVED:
                self._removed = True
            elif char in _SPACES:
                self._spaces += char
            elif char == "\n":
                if self._line_text or not self._removed:
                    self._breaks += 1
                self._spaces, self._removed, self._line_text = "", False, False
            else:
                out.append(self._gap(char))
                out.append(char)
                self._started, self._line_text = True, True
                self._spaces, self._breaks, self._removed = "", 0, False
        return "".join(out)

    def _gap(self, char: str) -> str:
        """Returns the whitespace released before the visible `char`."""
        if not self._started:
            return ""
        if self._breaks:
            if self._breaks > 2:
                self._fired["N5"] += 1
            return "\n" * min(self._breaks, 2) + ("" if self._removed else self._spaces)
        if not self._removed:
            return self._spaces
        return " " if self._spaces and char not in _CLOSING_PUNCTUATION else ""


def _end_on_sentence(text: str) -> str:
    """N4, for a reply that `finish_reason` `length` cut: returns `text` cut after its last sentence end when at least
    half of it stays, or else with `…` after it. A text that already ends on a sentence end, or is empty, is
    returned as it is."""
    if not text:
        return text
    ends = [match.end() for match in _SENTENCE_END.finditer(text)]
    cut = ends[-1] if ends else 0
    if cut == len(text):
        return text
    if cut and 2 * cut >= len(text):
        return text[:cut]
    return text.rstrip(_TRAILING_BEFORE_ELLIPSIS) + ELLIPSIS


class ReplyNormalizer:
    """Normalizes one reply as it streams: feed it the runtime's text in order, then call `finish` once.

    The text `feed` returns is final: no later call changes it, except that N4 may cut the end of a reply cut by
    length, which `NormalizedReply.tail` reports as "". The delivered text depends only on the whole input and the
    finish reason, never on how the input was split. Not thread-safe; one instance serves one generation.
    """

    def __init__(self, rules: NormalizerRules) -> None:
        self._fired: Counter[str] = Counter()
        stages: list[_Stage] = [
            _Opening(rules.names, self._fired),
            _Markdown(self._fired),
            _Narration(rules.narration, self._fired),
        ]
        if rules.emoji == "mai":
            stages.append(_Emoji(self._fired))
        stages.append(_Spacing(self._fired))
        self._stages = tuple(stages)
        self._released: list[str] = []
        self._finished = False

    def feed(self, text: str) -> str:
        """Takes the next piece of raw text and returns the normalized text it releases, often "". A NUL or carriage
        return in the input is dropped. Raises RuntimeError after `finish`."""
        if self._finished:
            raise RuntimeError("the reply was already finished")
        released = text.replace(_REMOVED, "").replace("\r", "")
        for stage in self._stages:
            if not released:
                break
            released = stage.feed(released)
        self._released.append(released)
        return released

    def finish(self, finish_reason: FinishReason | None) -> NormalizedReply:
        """Releases what was held back, applies N4 when `finish_reason` is `length`, and returns the delivered text.
        Raises RuntimeError when called twice."""
        if self._finished:
            raise RuntimeError("the reply was already finished")
        self._finished = True
        tail = ""
        for stage in self._stages:
            tail = stage.feed(tail) + stage.finish()
        released = "".join(self._released)
        text = released + tail
        if finish_reason == "length":
            ended = _end_on_sentence(text)
            self._fired["N4"] += int(ended != text)
            text = ended
        fired = {rule: count for rule, count in sorted(self._fired.items()) if count}
        return NormalizedReply(text, text[len(released) :] if text.startswith(released) else "", fired)


def normalize_reply(raw: str, rules: NormalizerRules, finish_reason: FinishReason | None = "stop") -> NormalizedReply:
    """Returns the normalization of a reply known in one piece: the same as feeding `raw` to a ReplyNormalizer in any
    split, then finishing it with `finish_reason`."""
    normalizer = ReplyNormalizer(rules)
    normalizer.feed(raw)
    return normalizer.finish(finish_reason)
