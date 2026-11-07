"""Tests of lyrveil/consistency/normalizer.py: T-CON-09 (rules N1 to N5 on fixed raw replies of each template family,
in one piece and split into stream chunks at every position) and the single rules.

The raw replies follow the habits of the profiles of each family (docs/models.md): Markdown from the `gemma4` instruct
models, and labels, asterisk narration, wrapping quotes and emoji from the roleplay finetunes of every family. They are
the text that reaches the normalizer after the stop sequences. The rules come from the three demo cards.
"""

from dataclasses import dataclass
from typing import get_args

import pytest

from lyrveil.catalog import TextTemplate
from lyrveil.consistency.normalizer import QUOTE_WINDOW, NormalizerRules, ReplyNormalizer, normalize_reply
from lyrveil.domain.card import CharacterCard, Narration
from lyrveil.ports.text_runtime import FinishReason
from lyrveil.services.demo_characters import DEMO_CARDS

CARDS = {card["identity"]["first_name"]: CharacterCard.model_validate(card) for card in DEMO_CARDS}
# Nerea: `nessuna`, emoji `raramente`. Ilario: `leggera`, emoji `mai`. Tessa: `nessuna`, emoji `spesso`.
NEREA, ILARIO, TESSA = (NormalizerRules.for_card(CARDS[name]) for name in ("Nerea", "Ilario", "Tessa"))
# Keeps every span and every emoji, so a test sees one rule at a time.
LOOSE = NormalizerRules(("Alba",), "libera", "spesso")


@dataclass(frozen=True)
class Case:
    """A raw reply of one family, the card rules it runs under, and what the normalizer must deliver."""

    id: str
    family: TextTemplate
    rules: NormalizerRules
    raw: str
    expected: str
    fired: dict[str, int]
    finish: FinishReason = "stop"


CASES = (
    Case(
        "gemma4-markdown",
        "gemma4",
        NEREA,
        "**Nerea:** Ciao! Sono appena tornata dal porto.  \n\n"
        "### Com'è andata\n\n"
        "- Il vento era **fortissimo**.\n"
        "- Ho perso un guanto.\n\n"
        "---\n\n"
        "*si toglie la giacca bagnata e la appende* Tu invece? 😊\n\n\n\n",
        "Ciao! Sono appena tornata dal porto.\n\nCom'è andata\n\n- Il vento era fortissimo.\n- Ho perso un guanto.\n\n"
        "Tu invece? 😊",
        {"N1": 1, "N2": 4, "N3": 1, "N5": 1},
    ),
    Case(
        "gemma4-roleplay-cut-by-length",
        "gemma4",
        TESSA,
        "Tessa - *ride e si sporge oltre il bancone* Ok, ok, ti preparo il solito! Anzi no, oggi provi una cosa "
        "nuova, fidati di me perché",
        "Ok, ok, ti preparo il solito! Anzi no, oggi provi una cosa nuova, fidati di me perché…",
        {"N1": 1, "N3": 1, "N4": 1},
        "length",
    ),
    Case(
        "mistral-label-narration-emoji",
        "mistral_v3_tekken",
        ILARIO,
        'Ilario: *si sistema gli occhiali e chiude il registro* "Buonasera. La aspettavo." '
        "*indica la sedia davanti alla scrivania* Prego, si accomodi. 🙂",
        '*si sistema gli occhiali e chiude il registro* "Buonasera. La aspettavo." Prego, si accomodi.',
        {"N1": 1, "N3": 1, "N5": 1},
    ),
    Case(
        "mistral-wrapped-quote-cut-by-length",
        "mistral_v3_tekken",
        TESSA,
        "Tessa Moravi: \"Ehi, finalmente! Ti ho tenuto da parte l'ultima fetta di torta al limone, ma solo perché sei "
        "tu. Se arriva Bruno",
        "Ehi, finalmente! Ti ho tenuto da parte l'ultima fetta di torta al limone, ma solo perché sei tu.",
        {"N1": 2, "N4": 1},
        "length",
    ),
    Case(
        "chatml-short-wrapped-quote",
        "chatml",
        NEREA,
        "  \N{LEFT DOUBLE QUOTATION MARK}Davvero? Non ci credo.\N{RIGHT DOUBLE QUOTATION MARK}\n\n\n",
        "Davvero? Non ci credo.",
        {"N1": 1},
    ),
    Case(
        "chatml-table-and-emoji-sequence",
        "chatml",
        ILARIO,
        "Ilario \N{EM DASH} Certo. Ecco l'orario della biblioteca:\n\n"
        "| Giorno | Orario |\n"
        "|---|---|\n"
        "| Lunedì | 9-13 |\n"
        "| **Sabato** | chiuso |\n\n"
        "*Le porge un foglio con gli orari stampati.* La aspetto lunedì. 👨\N{ZERO WIDTH JOINER}🏫✨",
        "Certo. Ecco l'orario della biblioteca:\n\nGiorno, Orario\nLunedì, 9-13\nSabato, chiuso\n\n"
        "*Le porge un foglio con gli orari stampati.* La aspetto lunedì.",
        {"N1": 1, "N2": 6, "N5": 1},
    ),
)


def splits(raw: str) -> list[list[str]]:
    """Returns `raw` cut in two at every position, then cut into equal chunks of 1 to 8 characters."""
    halves = [[raw[:index], raw[index:]] for index in range(len(raw) + 1)]
    chunked = [[raw[index : index + size] for index in range(0, len(raw), size)] for size in range(1, 9)]
    return halves + chunked


def run(raw: str, rules: NormalizerRules = NEREA, finish: FinishReason = "stop") -> str:
    return normalize_reply(raw, rules, finish).text


def test_t_con_09_covers_every_template_family() -> None:
    assert {case.family for case in CASES} == set(get_args(TextTemplate))


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_t_con_09_a_raw_reply_gives_the_same_delivered_text_in_one_piece_and_in_every_split(case: Case) -> None:
    whole = normalize_reply(case.raw, case.rules, case.finish)
    assert (whole.text, whole.fired) == (case.expected, case.fired)
    for chunks in splits(case.raw):
        normalizer = ReplyNormalizer(case.rules)
        released = "".join(normalizer.feed(chunk) for chunk in chunks)
        reply = normalizer.finish(case.finish)
        assert (reply.text, reply.fired) == (whole.text, whole.fired), chunks
        if reply.text.startswith(released):
            # What was shown, then the tail, is exactly the delivered text.
            assert released + reply.tail == reply.text, chunks
        else:
            # Only N4 takes back text already shown; message.completed carries the delivered text.
            assert (case.finish, reply.tail) == ("length", ""), chunks


def test_plain_text_is_released_at_once_and_only_what_may_still_change_is_held_back() -> None:
    normalizer = ReplyNormalizer(NEREA)
    assert normalizer.feed("Ciao") == "Ciao"
    assert normalizer.feed(" a te") == " a te"
    # An open span may be narration, which `nessuna` removes; the space before it may be left over.
    assert normalizer.feed(" *si") == ""
    assert normalizer.feed(" avvicina piano*") == ""
    assert normalizer.feed(" Eccomi.  ") == " Eccomi."
    reply = normalizer.finish("stop")
    assert (reply.text, reply.tail, reply.fired) == ("Ciao a te Eccomi.", "", {"N3": 1})
    assert reply.checks() == {"normalizer": {"N3": 1}}

    held = ReplyNormalizer(NEREA)
    assert held.feed("Ner") == "", "the start of the name may become a label"
    assert held.feed("o di seppia") == "Nero di seppia"
    quoted = ReplyNormalizer(NEREA)
    assert quoted.feed('"Ciao') == "", "an opening quote waits for the window"
    assert quoted.feed(" " * QUOTE_WINDOW) == "Ciao"


def test_a_normalizer_refuses_text_after_it_finished() -> None:
    normalizer = ReplyNormalizer(NEREA)
    normalizer.finish("stop")
    with pytest.raises(RuntimeError):
        normalizer.feed("Ciao")
    with pytest.raises(RuntimeError):
        normalizer.finish("stop")


def test_the_rules_come_from_the_card() -> None:
    assert NormalizerRules(("Ilario Sant'Elmo", "Ilario"), "leggera", "mai") == ILARIO
    card = CARDS["Nerea"]
    unnamed = card.model_copy(update={"identity": card.identity.model_copy(update={"last_name": None})})
    assert NormalizerRules.for_card(unnamed).names == ("Nerea",)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Nerea: Ciao.", "Ciao."),
        ("NEREA FALCHI: Ciao.", "Ciao."),
        ("**Nerea**: Ciao.", "Ciao."),
        ("Nerea \N{EN DASH} Ciao.", "Ciao."),
        ("\n\nNerea:\nCiao.", "Ciao."),
        ("Nerea:", ""),
        ("Nerea-chan è un soprannome.", "Nerea-chan è un soprannome."),
        ("Nerea è il mio nome.", "Nerea è il mio nome."),
        ("Nereide: un'altra.", "Nereide: un'altra."),
        ('"Ciao!" Ride e ti saluta.', '"Ciao!" Ride e ti saluta.'),
        ("«Ciao, come stai?»", "Ciao, come stai?"),
        ('Nerea: "Ciao!"', "Ciao!"),
        ('"Ciao', "Ciao"),
        ('Mi ha detto "vai".', 'Mi ha detto "vai".'),
        # No closing quote within the window: the opening quote goes with the first closing one, wherever it is.
        (
            '"Non ti aspettavo così presto, sai? Pensavo arrivassi stasera." Si sistema i capelli. "Entra pure."',
            'Non ti aspettavo così presto, sai? Pensavo arrivassi stasera. Si sistema i capelli. "Entra pure."',
        ),
    ],
)
def test_n1_removes_a_leading_label_and_wrapping_quotes_only(raw: str, expected: str) -> None:
    assert run(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected", "count"),
    [
        ("# Titolo\nTesto", "Titolo\nTesto", 1),
        ("#estate al mare", "#estate al mare", 0),
        ("Ecco:\n```python\nprint('ciao')\n# nota\n```\nFatto.", "Ecco:\nprint('ciao')\n# nota\nFatto.", 2),
        ("Prima\n***\nDopo ***davvero tanto***", "Prima\nDopo *davvero tanto*", 3),
        ("- uno\n- due", "- uno\n- due", 0),
        ("Prima\n\n| a | b |\n| :-- | --: |\n\nDopo", "Prima\n\na, b\n\nDopo", 2),
    ],
)
def test_n2_turns_markdown_into_plain_text(raw: str, expected: str, count: int) -> None:
    reply = normalize_reply(raw, LOOSE)
    assert (reply.text, reply.fired.get("N2", 0)) == (expected, count)


NARRATED = "*sorride* Ciao. *si avvicina piano alla finestra* Guarda. *indica il faro lontano* Bello, *vero*?"


@pytest.mark.parametrize(
    ("narration", "expected"),
    [
        ("nessuna", "*sorride* Ciao. Guarda. Bello, *vero*?"),
        ("leggera", "*sorride* Ciao. *si avvicina piano alla finestra* Guarda. Bello, *vero*?"),
        ("libera", NARRATED),
    ],
)
def test_n3_applies_narration_to_spans_of_three_words_or_more(narration: Narration, expected: str) -> None:
    rules = NormalizerRules(("Alba",), narration, "spesso")
    assert run(NARRATED, rules) == expected
    assert run("Ciao *ancora qui dentro", rules) == "Ciao *ancora qui dentro", "an unclosed asterisk stays"


@pytest.mark.parametrize(
    ("raw", "finish", "expected"),
    [
        ("Ciao. Come stai?", "length", "Ciao. Come stai?"),
        ("Oggi il porto è pieno di barche. E poi", "length", "Oggi il porto è pieno di barche."),
        ("Ciao a te. Oggi il porto è pieno di barche e", "length", "Ciao a te. Oggi il porto è pieno di barche e…"),
        ("Pensavo che,", "length", "Pensavo che…"),
        ("Oggi il porto è pieno di barche. E poi", "stop", "Oggi il porto è pieno di barche. E poi"),
        ("Oggi il porto è pieno di barche. E poi", "aborted", "Oggi il porto è pieno di barche. E poi"),
    ],
)
def test_n4_ends_a_reply_cut_by_length_on_a_sentence_or_an_ellipsis(
    raw: str, finish: FinishReason, expected: str
) -> None:
    assert run(raw, NEREA, finish) == expected


def test_n5_removes_emoji_only_for_mai_and_collapses_blank_lines_and_trailing_spaces() -> None:
    raw = "Ciao 😊! Ci vediamo \N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16} domani 👍"
    assert run(raw, ILARIO) == "Ciao! Ci vediamo domani"
    assert run(raw, TESSA) == raw
    reply = normalize_reply("Uno\n\n\n\nDue   \n   \nTre  ", NEREA)
    assert (reply.text, reply.fired) == ("Uno\n\nDue\n\nTre", {"N5": 1})
