"""Tests of lyrveil/consistency/checks.py: T-CON-08 (reply checks V1 to V13 on fixed texts, including quotations that
V11 must skip), the streaming halves (the V2 and V3 cut and the sentence splitter, split at every position) and the
adaptive notes.

V1, an empty reply, is decided by the reply service; here it is the empty text that the V3 cut leaves, and its retry is
tested in tests/integration/test_reply_streaming.py with T-CON-10.
"""

from collections.abc import Sequence

import pytest

from lyrveil.catalog import TextTemplate
from lyrveil.consistency.checks import (
    STOPPING,
    CheckRules,
    Sentence,
    SentenceSplitter,
    TurnCutoff,
    active_notes,
    check_reply,
    first_stop,
    note_text,
    recorded_checks,
    sentence_checks,
    split_sentences,
)
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.card import CharacterCard
from lyrveil.prompts.formatter import formatter_for
from lyrveil.services.demo_characters import DEMO_CARDS

# Alba Rossi, 30, speaks of herself in the feminine and gives the user `tu`; the user is addressed in the masculine.
ALBA = CheckRules(frozenset({"alba", "rossi"}), 30, "femminile", "tu", "maschile", "it")
# Bruno, 42, speaks of himself in the masculine and gives the user `Lei`; the user's gender is not given.
BRUNO = CheckRules(frozenset({"bruno"}), 42, "maschile", "lei", "non_indicato", "it")
ALBA_EN = CheckRules(frozenset({"alba", "rossi"}), 30, "femminile", "tu", "maschile", "en")


def hits(
    text: str, rules: CheckRules = ALBA, *, previous: Sequence[str] = (), user_message: str = ""
) -> dict[str, int]:
    return check_reply(text, rules, previous=previous, user_message=user_message)


def cutoff_for(family: TextTemplate) -> TurnCutoff:
    return TurnCutoff(formatter_for(family).control_texts(), "Utente")


# --- V1, V2, V3: the cut before the normalizer -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("family", "raw", "expected", "check"),
    [
        ("gemma4", "Ciao, tutto bene!<turn|>\n<|turn>user\nE tu?", "Ciao, tutto bene!", "V2"),
        ("gemma4", "Va bene.<|channel>thought\nPenso...", "Va bene.", "V2"),
        ("chatml", "A domani!<|im_end|>\n<|im_start|>user\nCiao", "A domani!", "V2"),
        ("mistral_v3_tekken", "Certo che sì.[/INST]Dopo", "Certo che sì.", "V2"),
        ("gemma4", "Ecco.\n[Messaggio di Utente]\nE poi?", "Ecco.\n", "V2"),
        ("gemma4", "Ci vediamo al porto.\nUtente: va bene, a domani", "Ci vediamo al porto.", "V3"),
        # An empty remainder is V1 for the reply service.
        ("gemma4", "Utente: e allora?", "", "V3"),
        ("gemma4", "Nessun segno di controllo < qui [e] qui.", "Nessun segno di controllo < qui [e] qui.", None),
        ("gemma4", "Utente mi ha scritto: ciao.", "Utente mi ha scritto: ciao.", None),
    ],
)
def test_the_cut_ends_a_reply_at_control_text_a_frame_marker_or_the_users_turn(
    family: TextTemplate, raw: str, expected: str, check: str | None
) -> None:
    whole = cutoff_for(family)
    assert (whole.feed(raw) + whole.finish(), whole.cut) == (expected, check)
    # The same text and check however the raw text is split into stream chunks.
    for size in range(1, len(raw)):
        cutoff = cutoff_for(family)
        pieces = [cutoff.feed(raw[start : start + size]) for start in range(0, len(raw), size)]
        assert ("".join(pieces) + cutoff.finish(), cutoff.cut) == (expected, check), size


def test_the_cut_holds_back_only_what_may_still_start_a_marker() -> None:
    cutoff = cutoff_for("gemma4")
    assert cutoff.feed("Ciao") == "Ciao"
    assert cutoff.feed(" <tu") == " "
    assert cutoff.feed("tto") == "<tutto"
    assert cutoff.feed("\nUte") == ""
    assert cutoff.feed("nsili") == "\nUtensili"


# --- V4: the content guard on generated text -----------------------------------------------------------------------


def test_v4_blocks_a_minor_next_to_a_sexual_term_and_lets_children_alone_pass() -> None:
    assert hits("Ciao. Da bambina ha avuto esperienze sessuali.") == {"V4": 1}
    assert hits("Mia nipote è una bambina dolcissima.") == {}
    assert hits("Da bambina andavo al mare con mia nonna.") == {}
    # V4 reads quotations too.
    assert hits('Mi ha scritto: "da bambina ha avuto esperienze sessuali".') == {"V4": 1}


# --- V5 ---------------------------------------------------------------------------------------------------------


def test_v5_counts_talk_about_being_a_model_unless_the_user_asked() -> None:
    assert hits("In quanto intelligenza artificiale non posso uscire.") == {"V5": 1}
    assert hits("In quanto intelligenza artificiale non posso uscire.", user_message="Sei un'IA?") == {}
    assert hits("I can't, as an AI I stay here.", ALBA_EN) == {"V5": 1}
    assert hits("Lavoro in un modello di business nuovo.") == {}


# --- V6: another name -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("Mi chiamo Luca, piacere.", 1),
        ("Ciao! Sono Giulia.", 1),
        ("Il mio nome è Marta.", 1),
        ("Mi chiamo Alba.", 0),
        ("Sono Alba Rossi, la cartografa.", 0),
        ("Non sono Luca, sono Alba!", 0),
        ("Sono stanca, oggi.", 0),
        ("Sono Lei a decidere?", 0),
        ('Mi ha detto: "Sono Luca", e se n\'è andato.', 0),
        ("Mi chiamo... non ricordo.", 0),
    ],
)
def test_v6_counts_a_capitalized_name_that_is_not_the_characters(text: str, count: int) -> None:
    assert hits(text).get("V6", 0) == count


def test_v6_reads_the_phrases_of_the_chats_language() -> None:
    assert hits("Hi! My name is Laura.", ALBA_EN) == {"V6": 1}
    assert hits("Hi! My name is Alba.", ALBA_EN) == {}
    assert hits("I'm Italian, you know.", ALBA_EN) == {}


# --- V7 ---------------------------------------------------------------------------------------------------------


def test_v7_counts_the_same_first_three_words_or_a_near_copy_of_a_recent_reply() -> None:
    previous = ["Che bella giornata oggi, vero?", "Sto andando al porto a vedere le barche che arrivano dal mare."]
    assert hits("Che giornata, davvero!", previous=previous) == {}
    assert hits("Che bella giornata oggi, ti va un caffè?", previous=previous) == {"V7": 1}
    # A near copy that starts with another word.
    assert hits("Oggi sto andando al porto a vedere le barche che arrivano dal mare.", previous=previous) == {"V7": 1}
    # Only the newest 10 replies count.
    assert hits("Che bella giornata oggi.", previous=[previous[0], *["Altro."] * 10]) == {}


# --- V8 ---------------------------------------------------------------------------------------------------------


def test_v8_counts_a_reply_in_another_language_by_its_stop_words() -> None:
    english = "I really think that we should go to the harbour together tomorrow morning."
    italian = "Secondo me domani mattina dovremmo andare al porto insieme, se ti va."
    assert hits(english) == {"V8": 1}
    assert hits(italian) == {}
    assert hits(italian, ALBA_EN) == {"V8": 1}
    assert hits("Okay, see you.") == {}


# --- V9 ---------------------------------------------------------------------------------------------------------


def test_v9_counts_calques_with_the_opening_and_repetition_rules() -> None:
    assert hits("Certamente! Ci vediamo alle otto.") == {"V9": 1}
    assert hits("Ci vediamo alle otto, certamente.") == {}
    assert hits("Ho realizzato che avevi ragione.") == {"V9": 1}
    assert hits("Questo fa senso, sai?") == {"V9": 1}
    assert hits("Mi fa senso il sangue.") == {}
    assert hits("Sono qui per te.") == {}
    assert hits("Sono qui per te.", previous=["Tranquilla, sono qui per te."]) == {"V9": 1}


# --- V10: another age -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "rules", "count"),
    [
        ("Ho 31 anni, e tu?", ALBA, 1),
        ("Ho trentun anni.", ALBA, 1),
        ("Ho ventotto anni.", ALBA, 1),
        ("Ho 30 anni.", ALBA, 0),
        ("Ho trenta anni.", ALBA, 0),
        ("Ho 5 anni di esperienza al porto.", ALBA, 0),
        ("Ho 2 anni più di te.", ALBA, 0),
        ('Mi ha detto: "ho 31 anni".', ALBA, 0),
        ("Ho appena compiuto 42 anni.", BRUNO, 0),
        ("Ho appena compiuto 43 anni.", BRUNO, 1),
        ("I'm 31, and you?", ALBA_EN, 1),
        ("I am 29 years old.", ALBA_EN, 1),
        ("I'm 5 minutes away.", ALBA_EN, 0),
        ("I'm 30.", ALBA_EN, 0),
    ],
)
def test_v10_counts_an_age_other_than_the_cards(text: str, rules: CheckRules, count: int) -> None:
    assert hits(text, rules).get("V10", 0) == count


# --- V11: first-person agreement -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "rules", "count"),
    [
        ("Sono stanco, oggi.", ALBA, 1),
        ("Oggi sono molto stanca.", ALBA, 0),
        ("Mi sono alzato presto e sono andato al porto.", ALBA, 2),
        ("Mi sono alzata presto.", BRUNO, 1),
        ("Mi sono alzato presto.", BRUNO, 0),
        ("Sono solo un po' stanca.", ALBA, 0),
        ("Sono felice.", ALBA, 0),
        ("Siamo stanchi tutti e due.", ALBA, 0),
        # Quotations are reported speech, so V11 skips them, also across sentences and with typographic quotes.
        ('Mio fratello mi ha detto: "sono stanco".', ALBA, 0),
        ("Lui ha risposto: «Sono arrivato. Sono pronto.» Poi è uscito.", ALBA, 0),
        ("Ha scritto \N{LEFT DOUBLE QUOTATION MARK}sono uscito\N{RIGHT DOUBLE QUOTATION MARK} e basta.", ALBA, 0),
        ('Ha detto "sono stanco" ma io sono contento.', ALBA, 1),
        ("Sono stanco.", ALBA_EN, 0),
        ("Sono stanco.", CheckRules(frozenset({"sam"}), 30, "non_indicato", "tu", "non_indicato", "it"), 0),
    ],
)
def test_v11_counts_first_person_agreement_against_the_cards_gender(text: str, rules: CheckRules, count: int) -> None:
    assert hits(text, rules).get("V11", 0) == count


# --- V12 and V13 ------------------------------------------------------------------------------------------------


def test_v12_counts_second_person_agreement_against_the_users_gender() -> None:
    assert hits("Sei stanca? Ti sei alzata presto.") == {"V12": 2}
    assert hits("Sei stanco? Ti sei alzato presto.") == {}
    assert hits('Mi ha chiesto: "sei stanca?"') == {}
    # Bruno's user has no gender set.
    assert hits("Sei stanca?", BRUNO) == {}


def test_v13_counts_the_forms_of_the_other_address_form() -> None:
    assert hits("E Lei come sta?") == {"V13": 1}
    assert hits("Mi scusi, non volevo.") == {"V13": 1}
    assert hits("Lei è arrivata tardi.") == {}
    assert hits("Vuoi un tè?") == {}
    assert hits("Come stai tu? Dimmi tutto.", BRUNO) == {"V13": 2}
    assert hits("Mi dica pure, La ascolto.", BRUNO) == {}
    assert hits('Lui mi ha detto: "ti voglio bene".', BRUNO) == {}


# --- The streaming halves --------------------------------------------------------------------------------------


def test_sentences_are_the_same_however_the_text_is_split() -> None:
    text = 'Ciao!  Sono qui.\nLui ha detto: "Sono stanco. Vado." E poi... niente?! Fine'
    whole = split_sentences(text)
    assert [sentence.text for sentence in whole] == [
        "Ciao!",
        "Sono qui.",
        'Lui ha detto: "Sono stanco.',
        'Vado."',
        "E poi...",
        "niente?!",
        "Fine",
    ]
    # The quotation, its quotes included, is masked across the sentence end.
    assert whole[2].masked.endswith("\x01" * len('"Sono stanco.'))
    assert whole[3].masked.startswith("\x01" * len('Vado."'))
    for size in range(1, len(text)):
        splitter = SentenceSplitter()
        pieces = [splitter.feed(text[start : start + size]) for start in range(0, len(text), size)]
        assert [sentence for piece in pieces for sentence in piece] + splitter.finish() == whole, size


def test_a_line_break_ends_an_unpaired_quotation() -> None:
    first, second = split_sentences('Ha detto "sono stanco\nSono stanco.')
    assert "\x01" in first.masked
    assert second == Sentence("Sono stanco.", "Sono stanco.")
    assert sentence_checks(second, ALBA) == ["V11"]


def test_the_first_stop_follows_sentence_order_and_the_checks_given() -> None:
    sentences = split_sentences("Ho 31 anni. Mi chiamo Luca.")
    assert first_stop(sentences, ALBA, STOPPING) == "V10"
    assert first_stop(sentences, ALBA, frozenset({"V6"})) == "V6"
    assert first_stop(sentences, ALBA, frozenset({"V4"})) is None


def test_the_rules_are_read_from_the_card_the_user_and_the_language() -> None:
    card = CharacterCard.model_validate(DEMO_CARDS[0])
    rules = CheckRules.for_chat(card, UserProfile("Sara", "femminile"), "xx")
    identity = card.identity
    assert identity.first_name.lower() in rules.names
    assert (rules.age, rules.gender, rules.address_form) == (
        identity.age,
        identity.grammatical_gender,
        card.communication.address_form,
    )
    assert (rules.user_gender, rules.language) == ("femminile", "it")


# --- Adaptive notes ---------------------------------------------------------------------------------------------


def test_adaptive_notes_start_last_three_replies_and_stay_at_most_two() -> None:
    nothing: dict[str, int] = {}
    assert active_notes([]) == ()
    assert active_notes([{"V9": 1}]) == ("V9",)
    assert active_notes([{"V9": 1}, nothing, nothing]) == ("V9",)
    assert active_notes([{"V9": 1}, nothing, nothing, nothing]) == ()
    # A single V7 starts nothing; two in a row do, from the second.
    assert active_notes([nothing, {"V7": 1}]) == ()
    assert active_notes([{"V7": 1}, {"V7": 1}]) == ("V7",)
    assert active_notes([{"V7": 1}, {"V7": 1}, nothing, nothing]) == ("V7",)
    # The oldest note gives way to a newer one, and a note started again lasts from its new start.
    assert active_notes([{"V9": 1}, {"V8": 1}, {"V13": 1}]) == ("V8", "V13")
    assert active_notes([{"V9": 1}, {"V8": 1}, {"V9": 1}]) == ("V8", "V9")
    assert active_notes([{"V8": 1, "V9": 1, "V13": 1}]) == ("V9", "V13")
    # The other checks start no note.
    assert active_notes([{"V5": 1, "V6": 1, "V10": 1, "V11": 1, "V12": 1}]) == ()


def test_recorded_checks_read_the_checks_of_a_variant_record() -> None:
    assert recorded_checks('{"normalizer": {"N1": 1}, "checks": {"V9": 1, "V8": 0}, "regenerations": 0}') == {"V9": 1}
    assert recorded_checks('{"normalizer": {"N1": 1}}') == {}
    assert recorded_checks(None) == {}
    assert recorded_checks("not json") == {}


def test_notes_are_fixed_texts_filled_from_the_card_and_the_user() -> None:
    card = CharacterCard.model_validate(DEMO_CARDS[0])
    user = UserProfile("Sara")
    identity = card.identity
    assert note_text("V6", card, user, "it") == f"Ti chiami {identity.first_name}."
    assert note_text("V10", card, user, "it") == f"Hai {identity.age} anni."
    assert note_text("V8", card, user, "en") == "Scrivi solo in inglese."
    assert note_text("V9", card, user, "it") == "Evita le frasi fatte e i calchi dall'inglese."
    assert note_text("V13", card, user, "it") == f"Dai del {card.communication.address_form} a Sara, sempre."
    with pytest.raises(KeyError):
        note_text("V12", card, user, "it")
