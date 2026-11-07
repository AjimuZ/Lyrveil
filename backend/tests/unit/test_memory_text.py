"""Checks the memory index text: T-MEM-06 (Italian stemming and accents) and T-MEM-07 (FTS syntax is harmless)."""

import re
import sqlite3
from collections.abc import Iterator

import pytest

from lyrveil.memory.text import (
    STOPWORD_DIR,
    index_text,
    language_code,
    match_query,
    normalize,
    stems,
    stop_words,
    supported_languages,
)

QUOTED_TERMS = re.compile(r'^"[^\W_]+"( OR "[^\W_]+")*$')


@pytest.fixture
def index() -> Iterator[sqlite3.Connection]:
    """Yields an in-memory FTS5 table shaped like `memory_fts`, filled with three Italian memories."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE VIRTUAL TABLE memory_fts USING fts5(memory_id UNINDEXED, content_stem, subject_stem, "
        'tokenize = "unicode61 remove_diacritics 2")'
    )
    rows = {
        "job": ("Lavoravo allo Studio Rossi, in città.", "user.employer"),
        "cat": ("Ha due gatti neri.", "user.pets"),
        "dog": ("Il cane bianco è di sua sorella.", None),
    }
    for memory_id, (content, subject) in rows.items():
        stemmed = (index_text(content, "it"), index_text(subject or "", "it"))
        connection.execute("INSERT INTO memory_fts VALUES (?, ?, ?)", (memory_id, *stemmed))
    yield connection
    connection.close()


def search(connection: sqlite3.Connection, user_text: str, language: str = "it") -> set[str]:
    """Runs `user_text` through `match_query` and returns the ids of the rows it finds, none for an empty query."""
    match = match_query(user_text, language)
    if match is None:
        return set()
    rows = connection.execute("SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ?", (match,))
    return {row[0] for row in rows}


def test_italian_inflections_share_a_stem() -> None:
    assert len({*stems("lavoro", "it"), *stems("lavoravo", "it"), *stems("lavorare", "it")}) == 1
    assert stems("gatto", "it") == stems("gatti", "it")
    assert stems("abitavo", "it") == stems("abito", "it")


def test_a_word_with_and_without_its_accent_gives_the_same_stem() -> None:
    assert stems("perché", "it") == stems("perche", "it")
    assert stems("Città", "it") == stems("citta", "it") == stems("CITTÀ", "it")
    assert stems("più", "it") == stems("piu", "it")
    assert normalize("Perché Così") == "perche cosi"


def test_italian_queries_find_other_forms_and_unaccented_text(index: sqlite3.Connection) -> None:
    assert search(index, "lavoro") == {"job"}
    assert search(index, "Dove lavoravo prima?") == {"job"}
    assert search(index, "citta") == {"job"}
    assert search(index, "CITTÀ") == {"job"}
    assert search(index, "il gatto nero") == {"cat"}
    assert search(index, "sorella") == {"dog"}
    assert search(index, "employer") == {"job"}
    assert search(index, "astronave") == set()


def test_stop_words_are_dropped_before_stemming() -> None:
    assert stems("Il gatto di Luca", "it") == stems("gatto Luca", "it")
    assert stems("the cat and the dog", "en") == stems("cat dog", "en")
    assert stems("il la di", "it") == []
    assert index_text("Il gatto", "it") == " ".join(stems("gatto", "it"))


def test_the_other_languages_stem_with_their_own_algorithm() -> None:
    assert stems("running", "en") == stems("runs", "en")
    assert stems("chats", "fr") == stems("chat", "fr")
    assert stems("Häuser", "de") == stems("hauser", "de")
    assert stems("gatos", "es") == stems("gato", "es")


def test_a_regional_tag_selects_the_language_and_an_unknown_one_skips_stemming() -> None:
    assert stems("lavoravo", "it-IT") == stems("lavoravo", "it")
    assert stems("lavoravo", "IT_it") == stems("lavoravo", "it")
    assert stems("Gatti e Cani", "xx") == ["gatti", "e", "cani"]
    assert stems("Gatti e Cani", None) == ["gatti", "e", "cani"]
    assert language_code("") is None
    assert language_code(None) is None
    assert language_code("pt-BR") == "pt"


def test_every_supported_language_has_a_clean_stop_word_list() -> None:
    assert set(supported_languages()) == {"it", "en", "fr", "de", "es"}
    assert {path.stem for path in STOPWORD_DIR.glob("*.txt")} == set(supported_languages())
    for language in supported_languages():
        words = stop_words(language)
        assert len(words) >= 40
        assert all(re.fullmatch(r"[^\W_]+", word) and word == normalize(word) for word in words)
    assert stop_words("xx") == frozenset()
    # Content words that an extraction can produce must never be filtered out.
    assert not {"lavoro", "gatto", "casa", "amico"} & stop_words("it")


@pytest.mark.parametrize(
    "hostile",
    [
        "gatto AND NOT cane",
        "NEAR(gatto cane)",
        '"gatto',
        'gatto" OR "x',
        "content_stem:gatto",
        "{content_stem subject_stem}: gatto",
        "gatt*",
        "^gatto",
        "gatto - cane",
        "(((",
        ")gatto(",
        "*",
        "'; DROP TABLE memory_fts; --",
        "memory_id:job",
        "NOT",
        "OR OR OR",
    ],
)
def test_fts_syntax_in_user_text_is_only_ever_words(index: sqlite3.Connection, hostile: str) -> None:
    match = match_query(hostile, "it")
    assert match is None or QUOTED_TERMS.fullmatch(match)
    # Running it must neither raise a syntax error nor read the text as an operator or a column filter.
    found = search(index, hostile)
    assert found <= {"job", "cat", "dog"}
    assert index.execute("SELECT count(*) FROM memory_fts").fetchone() == (3,)


def test_operators_become_plain_words_that_match_like_any_other(index: sqlite3.Connection) -> None:
    # As an operator, "NOT cane" would exclude the dog and "AND" would need both words. As words, either one matches.
    assert search(index, "gatti NOT cane") == {"cat", "dog"}
    assert search(index, "gatti AND cane") == {"cat", "dog"}
    assert search(index, "gatt*") == {"cat"}
    assert search(index, "memory_id:job") == set()
    assert search(index, "!!! ??? ...") == set()


def test_a_query_is_distinct_quoted_stems_joined_by_or() -> None:
    match = match_query("Lavoravo lavoro lavorare a Milano", "it")
    assert match is not None
    assert QUOTED_TERMS.fullmatch(match)
    assert match.count(" OR ") == 1
    assert match_query("", "it") is None
    assert match_query("   ", "it") is None
    assert match_query("il la di", "it") is None
