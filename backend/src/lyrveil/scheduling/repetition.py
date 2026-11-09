"""The repetition check of proactive messages (docs/scheduler.md, Generation flow, Checks): a new message is too
similar to a recent one when their words are alike. Pure: no database, no clock."""

import difflib
import re
import unicodedata
from collections.abc import Sequence

# A message at least this similar to one of the last RECENT_PROACTIVE proactive messages is discarded.
SIMILARITY = 0.8
RECENT_PROACTIVE = 20

_WORD = re.compile(r"\w+")


def _words(text: str) -> list[str]:
    """Returns the lowercase words of `text` with accents and combining marks removed, so punctuation, case and
    accents never make two messages look different."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    return _WORD.findall("".join(char for char in folded if not unicodedata.combining(char)))


def similarity(first: str, second: str) -> float:
    """Returns the similarity of two texts in 0 to 1: difflib's ratio over their words. Two texts with no word at all
    are not alike (0.0)."""
    one, other = _words(first), _words(second)
    if not one or not other:
        return 0.0
    return difflib.SequenceMatcher(None, one, other, autojunk=False).ratio()


def is_repetition(text: str, recent: Sequence[str]) -> bool:
    """Tells whether `text` has a similarity of at least SIMILARITY to one of the `recent` messages."""
    return any(similarity(text, earlier) >= SIMILARITY for earlier in recent)
