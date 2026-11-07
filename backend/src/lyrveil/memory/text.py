"""Prepares text for the memory index: normalization, stop-word removal, Snowball stemming and safe queries."""

import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Protocol

import snowballstemmer

STOPWORD_DIR = Path(__file__).resolve().parent / "stopwords"

# Language code of a memory -> Snowball algorithm. A language that is not listed is indexed without stemming.
_ALGORITHMS = {
    "it": "italian",
    "en": "english",
    "fr": "french",
    "de": "german",
    "es": "spanish",
}

# Letters and digits only. The underscore is left out because the index tokenizer treats it as a separator.
_WORD = re.compile(r"[^\W_]+")


def normalize(text: str) -> str:
    """Returns `text` in NFKC, lowercase, with accents removed ("Perché" becomes "perche").

    Accents are folded before stemming, not only by the index tokenizer, so a word typed with and without its accent
    gives the same stem even where the stemmer treats the two spellings differently.
    """
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).lower())
    return unicodedata.normalize("NFC", "".join(char for char in decomposed if not unicodedata.combining(char)))


def language_code(language: str | None) -> str | None:
    """Returns the primary subtag of a BCP-47 tag in lowercase ("it-IT" gives "it"), or None for an empty value."""
    if not language:
        return None
    return language.replace("_", "-").split("-", 1)[0].strip().lower() or None


def supported_languages() -> tuple[str, ...]:
    """Returns the language codes that have a stemmer and a stop-word list."""
    return tuple(_ALGORITHMS)


@cache
def stop_words(language: str) -> frozenset[str]:
    """Returns the normalized stop words of a language code; an empty set when the language has no list.

    The list is the file `stopwords/<code>.txt`: one word per line, blank lines and `#` comments ignored. Words are
    normalized like the text they are compared with, so the files may keep their accents.
    """
    path = STOPWORD_DIR / f"{language}.txt"
    if language not in _ALGORITHMS or not path.is_file():
        return frozenset()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return frozenset(normalize(line) for line in lines if line)


class _Stemmer(Protocol):
    """The one method of a Snowball stemmer that this module uses; the library ships no type information."""

    def stemWord(self, word: str) -> str: ...  # noqa: N802


@cache
def _stemmer(algorithm: str) -> _Stemmer:
    stemmer: _Stemmer = snowballstemmer.stemmer(algorithm)
    return stemmer


def stems(text: str, language: str | None) -> list[str]:
    """Returns the stems of the words of `text`, in order and with repeats, after removing the stop words.

    Steps (docs/memory-engine.md, Stemming): normalize, split into words, drop the stop words of `language`, stem with
    Snowball. A language without a stemmer keeps the normalized words. The result never contains an empty string and
    holds only letters and digits.
    """
    code = language_code(language)
    words = _WORD.findall(normalize(text))
    algorithm = _ALGORITHMS.get(code or "")
    if code is None or algorithm is None:
        return words
    ignored = stop_words(code)
    stemmer = _stemmer(algorithm)
    stemmed = (stemmer.stemWord(word) for word in words if word not in ignored)
    # The filter is a guard: a stem that were not a plain word could otherwise reach a MATCH expression.
    return [stem for stem in stemmed if _WORD.fullmatch(stem)]


def index_text(text: str, language: str | None) -> str:
    """Returns the text to store in a `*_stem` column: the stems separated by single spaces."""
    return " ".join(stems(text, language))


def match_query(text: str, language: str | None) -> str | None:
    """Returns an FTS5 MATCH expression for the words of `text`, or None when no word is left to search for.

    Each distinct stem becomes a double-quoted string and the strings are joined with OR, so a row that holds any of
    them matches and ranking decides the order. Stems hold only letters and digits, so user text can never add an
    operator, a column filter, a prefix star or a quote: `AND`, `NOT`, `NEAR`, `content_stem:x`, `x*`, `^x` and
    unbalanced quotes or parentheses all become plain words. The same normalization and stemming as `index_text` run
    first, so the query and the index agree.
    """
    unique = dict.fromkeys(stems(text, language))
    if not unique:
        return None
    return " OR ".join(f'"{stem}"' for stem in unique)
