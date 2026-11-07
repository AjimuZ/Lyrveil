"""The deterministic image request detector (docs/image-generation.md, Manual mode): tells whether a chat message asks
the character for an image. No model, no I/O except the cached word lists of `prompts/<code>/`.

A message is a request when one of its sentences holds a request verb followed, at most MAX_DISTANCE words later, by an
image noun, and the verb is neither negated nor the verb of a statement. A language without lists never matches.
"""

import re
from functools import cache

from lyrveil.memory.text import language_code, normalize
from lyrveil.memory.window import PATTERN_DIR

VERBS_FILE = "image_request_verbs.txt"
NOUNS_FILE = "image_request_nouns.txt"
NEGATIONS_FILE = "image_request_negations.txt"
NOT_IMPERATIVE_FILE = "image_request_not_imperative.txt"
# The noun is at most this many words after the verb's last word ("mandami una tua bella foto": 4).
MAX_DISTANCE = 6
# A negation among this many words before the verb, in its clause, cancels the request.
NEGATION_REACH = 3

_SENTENCE_END = re.compile(r"[.!?;\n]+")
# A word, or a comma, which ends a clause for the negation and statement rules but not for the sentence.
_TOKEN = re.compile(r"[^\W_]+|,")
_COMMA = ","


@cache
def _phrases(language: str | None, file_name: str) -> frozenset[tuple[str, ...]]:
    """Returns the phrases of `prompts/<code>/<file_name>` as tuples of normalized words; empty when the language has
    no such list. One phrase per line, blank lines and `#` comments ignored."""
    code = language_code(language)
    if code is None or not code.isalpha():
        return frozenset()
    path = PATTERN_DIR / code / file_name
    if not path.is_file():
        return frozenset()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return frozenset(tuple(_TOKEN.findall(normalize(line))) for line in lines if line)


def _spans(words: list[str], phrases: frozenset[tuple[str, ...]]) -> list[tuple[int, int]]:
    """Returns the (start, end) word ranges, end excluded, where a phrase of `phrases` occurs."""
    found: list[tuple[int, int]] = []
    for phrase in phrases:
        size = len(phrase)
        for start in range(len(words) - size + 1):
            if tuple(words[start : start + size]) == phrase:
                found.append((start, start + size))
    return found


def detect_image_request(text: str, language: str | None) -> bool:
    """Tells whether `text`, a user's message in `language`, asks for an image.

    Case and accents are ignored. Each sentence (ended by . ! ? ; or a line break) is read on its own: a request verb
    counts when no negation stands among the NEGATION_REACH words before it in its clause, the word right before it
    in the clause is not one that makes a statement of it, and an image noun follows within MAX_DISTANCE words. Pure
    and deterministic: the same text always gets the same answer.
    """
    verbs = _phrases(language, VERBS_FILE)
    nouns = {phrase[0] for phrase in _phrases(language, NOUNS_FILE) if len(phrase) == 1}
    if not verbs or not nouns:
        return False
    negations = {phrase[0] for phrase in _phrases(language, NEGATIONS_FILE) if len(phrase) == 1}
    statements = {phrase[0] for phrase in _phrases(language, NOT_IMPERATIVE_FILE) if len(phrase) == 1}
    for sentence in _SENTENCE_END.split(normalize(text)):
        words: list[str] = []
        clause_starts: set[int] = {0}
        for token in _TOKEN.findall(sentence):
            if token == _COMMA:
                clause_starts.add(len(words))
            else:
                words.append(token)
        noun_positions = [position for position, word in enumerate(words) if word in nouns]
        if not noun_positions:
            continue
        for start, end in _spans(words, verbs):
            if _is_request(words, clause_starts, start, end, negations, statements, noun_positions):
                return True
    return False


def _is_request(
    words: list[str],
    clause_starts: set[int],
    start: int,
    end: int,
    negations: set[str],
    statements: set[str],
    noun_positions: list[int],
) -> bool:
    """Tells whether the verb at words[start:end] opens a request: see detect_image_request."""
    reach = start
    while reach > 0 and reach not in clause_starts and start - reach < NEGATION_REACH:
        reach -= 1
        if words[reach] in negations:
            return False
    if start > 0 and start not in clause_starts and words[start - 1] in statements:
        return False
    return any(end <= position < end + MAX_DISTANCE for position in noun_positions)
