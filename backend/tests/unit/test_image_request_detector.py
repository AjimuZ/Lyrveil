"""T-IMG-03: the request detector on at least 50 positive and 50 negative phrases (docs/image-generation.md, Manual
mode)."""

import pytest

from lyrveil.images.request_detector import detect_image_request

POSITIVE = [
    "Mandami una foto!",
    "Mandami una foto di te al porto",
    "Ciao Nerea, mandami una tua foto",
    "Inviami un selfie",
    "Per favore inviami una fotografia",
    "Fammi vedere una foto",
    "Fammi vedere un'immagine del mercato",
    "Mostrami un ritratto",
    "Mostrami il tuo disegno",
    "Disegna un ritratto di te",
    "Disegnami un paesaggio, uno schizzo veloce",
    "Genera un'immagine del porto al tramonto",
    "Generami un'illustrazione della mappa",
    "Crea un'immagine della nostra scena",
    "Creami un disegno",
    "Fammi una foto",
    "Scatta una foto al tramonto",
    "Scatta un selfie",
    "MANDAMI UNA FOTO",
    "mandami una foto",
    "Mandami   una\tfoto",
    "Dai, mandami una foto di quando eri al mare",
    "Ti prego, fammi vedere il tuo ritratto",
    "Ora disegna un" + chr(0x2019) + "illustrazione del faro",
    "Adesso crea un'immagine della stanza",
    "Ok, scatta un selfie con il cappello",
    "Mostrami una foto della tua stanza",
    "Mandami delle foto",
    "Mandami due selfie",
    "Disegnami degli schizzi del villaggio",
    "Mostrami le immagini del viaggio",
    "Inviami le fotografie dell'altra sera",
    "Creami tre illustrazioni",
    "Genera dei ritratti di noi due",
    "Fammi vedere i tuoi disegni",
    "Ehi, mandami una foto stasera",
    "Mandami una foto adesso, per favore",
    "Quando puoi, mandami una foto",
    "Mandami, per favore, una foto",
    "Se puoi mandami una foto",
    "Scatta una foto e mandamela",
    "Disegna un ritratto del tuo capitano",
    "Mostrami un disegno del tuo quaderno",
    "Crea un'illustrazione per la copertina",
    "Genera un ritratto realistico di te",
    "Fammi un selfie",
    "Mandami una foto del tuo gatto. Poi parliamo",
    "Come stai? Mandami una foto",
    "Sto aspettando: mandami una foto",
    "Inviami un'immagine appena puoi",
    "Mostrami una tua foto da bambina",
    "Mandami una sua fotografia",
    "Disegna una bella illustrazione del bosco incantato",
    "Per piacere mostrami un'illustrazione",
    "Mandami un'altra foto",
    "Ciao! Fammi vedere uno schizzo",
    "Scatta una fotografia del tramonto dalla finestra",
    "Su, creami un disegno",
    "Perché non dormi? Mandami una foto",
]

NEGATIVE = [
    "Non mandarmi foto",
    "Non mandarmi più immagini",
    "Ti ho mandato una foto",
    "Che bella foto!",
    "Ho visto una foto bellissima",
    "La foto che mi hai mandato è splendida",
    "Non mi mandare nessuna foto",
    "Mandami un messaggio",
    "Mostrami la mappa",
    "Disegna una linea retta",
    "Crea una lista della spesa",
    "Genera un numero a caso",
    "Scatta, andiamo!",
    "Fammi sapere domani",
    "Fammi un favore",
    "Lei crea immagini bellissime",
    "Chi disegna ritratti qui?",
    "Marco non crea mai immagini",
    "Non crea immagini da anni",
    "Non disegna ritratti da anni",
    "Se genera un'immagine ci avvisa",
    "Quando crea un disegno sorride",
    "Mentre disegna un ritratto canta",
    "Ho una foto del porto",
    "Mi piace il tuo ritratto",
    "Quella foto mi ha fatto ridere",
    "Ieri ho scattato una foto",
    "Hai creato un disegno?",
    "Ha disegnato un ritratto",
    "Il selfie è venuto bene",
    "Ciao, come stai?",
    "Che disegno meraviglioso",
    "Un'immagine vale più di mille parole",
    "Vorrei una foto",
    "Ho fatto una foto",
    "Mi ha mandato un ritratto",
    "Mostrami la mappa del porto, la strada, il faro, la piazza e il molo con una foto",
    "Disegna a lungo senza mai fermarti nel giardino di casa tua la foto",
    "Il fotografo lavora tutto il giorno",
    "Mandami un messaggio. Ho una foto.",
    "Mostrami la strada. La foto è sul tavolo.",
    "Ti ho detto di non mandarmi immagini",
    "Domani mi fai vedere la foto?",
    "Hai mandato la foto?",
    "Sto creando un disegno",
    "Abbiamo creato un ritratto",
    "Lui scatta foto bellissime",
    "Come crea un disegno così bello?",
    "Non so se mi mandi una foto",
    "Mai e poi mai mandami una foto",
    "Nessuna foto mi piace quanto questa",
    "Oggi il mare era calmo",
    "Che ne dici di un disegno",
    "Ormai non scatta più foto",
    "Perché crea immagini?",
    "Dove genera le immagini?",
    "Ho creato un'illustrazione ieri",
    "",
    "   ",
]


def test_the_lists_are_large_enough() -> None:
    assert len(POSITIVE) >= 50
    assert len([phrase for phrase in NEGATIVE if phrase.strip()]) >= 50


@pytest.mark.parametrize("message", POSITIVE)
def test_an_imperative_verb_and_an_image_noun_make_a_request(message: str) -> None:
    assert detect_image_request(message, "it") is True


@pytest.mark.parametrize("message", NEGATIVE)
def test_negations_statements_and_missing_nouns_are_not_requests(message: str) -> None:
    assert detect_image_request(message, "it") is False


def test_the_noun_must_come_within_six_words_of_the_verb() -> None:
    assert detect_image_request("mandami a b c d e foto", "it") is True
    assert detect_image_request("mandami a b c d e f foto", "it") is False


def test_the_noun_must_follow_the_verb() -> None:
    assert detect_image_request("Una foto, grazie. Mandami", "it") is False


def test_a_negation_in_another_clause_does_not_cancel_the_request() -> None:
    assert detect_image_request("Non so, mandami una foto", "it") is True


def test_a_language_tag_selects_the_list_by_its_primary_subtag() -> None:
    assert detect_image_request("Mandami una foto", "it-IT") is True
    assert detect_image_request("Mandami una foto", "IT") is True


@pytest.mark.parametrize("language", [None, "", "en", "xx", "../it"])
def test_a_language_without_lists_never_matches(language: str | None) -> None:
    assert detect_image_request("Mandami una foto", language) is False
