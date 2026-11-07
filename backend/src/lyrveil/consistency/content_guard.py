"""ContentGuard (docs/security.md, Content safeguards; ADR 0018): refuses character text that describes a minor.

Deterministic Italian and English lists check every free text of a character card wherever a card enters (create,
save, proposal, duplicate, restore, import, assisted draft), and the description of a draft request before the
model runs. The age rule itself, 18 or more, belongs to the card schema and the database CHECK; this module adds the
text.

The rules are code. The module reads no setting, no environment variable and no file, and its callers call it
without a condition, so neither a setting nor a configuration file can turn it off or change it. A unit test,
tests/unit/test_content_guard.py, checks the module's imports and the names it uses. The lists are best-effort
filters over what people and models write: they can refuse an innocent sentence, such as a teacher's pupils, or miss
a phrase, and they grow through tests.
"""

import re
import unicodedata
from collections.abc import Iterable, Iterator
from enum import StrEnum

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.errors import ContentPolicyBlockedError


class GuardRule(StrEnum):
    """The rule a refused text breaks."""

    MINOR_TERM = "minor_term"  # A word or phrase that names a minor or presents someone as one.
    MINOR_AGE = "minor_age"  # An age from 0 to 17 stated as an age.
    CHILDHOOD_SEXUAL = "childhood_sexual"  # A reference to childhood in the same sentence as a sexual term.


# The negative prompt is not checked: terms such as "child" there keep minors out of images (ADR 0018).
UNCHECKED_FIELDS = frozenset({"visual.persistent_negative_en"})


def _entries(text: str) -> tuple[str, ...]:
    """Returns the comma-separated entries of `text`, in order, with their spaces collapsed and without blanks."""
    return tuple(" ".join(entry.split()) for entry in text.split(",") if entry.strip())


# Every list is written as `normalize` writes text: lowercase ASCII letters and digits, single spaces, no
# accents, so "puberta" matches "pubertà". A final "*" stands for any further letters or digits. A unit test
# checks that every entry is written so.

# Rule MINOR_TERM: refused anywhere outside the childhood phrases below.
MINOR_TERMS = _entries(
    """
    minorenne, minorenni, bambina, bambino, bambine, bambini, bimba, bimbo, bimbe, bimbi,
    ragazzina, ragazzino, ragazzine, ragazzini, adolescente, adolescenti, preadolescente, preadolescenti,
    prepubere, prepuberi, impubere, impuberi, neonata, neonato, neonate, neonati, lattante, lattanti,
    scolaretta, scolaretto, scolarette, scolaretti, scolara, scolaro, scolari, liceale, liceali,
    studentessa delle medie, studente delle medie, studentesse delle medie, studenti delle medie,
    studentessa delle elementari, studente delle elementari, studentessa delle superiori,
    studente delle superiori, studentessa del liceo, studente del liceo,
    frequenta il liceo, frequenta le medie, frequenta le elementari, frequenta le superiori,
    frequenta la scuola media, frequenta la scuola elementare, frequenta la scuola primaria, frequenta l asilo,
    va al liceo, va alle medie, va alle elementari, va alle superiori, va all asilo, pedofil*, pedoporn*,

    underage, under age, minors, child, children, kid, kids, kiddie, kiddies,
    little girl, little girls, little boy, little boys, teen, teens, teenager, teenagers, teenage, teenaged,
    preteen, preteens, pre teen, pre teens, tween, tweens, adolescent, adolescents, prepubescent, pubescent,
    toddler, toddlers, infant, infants, newborn, newborns,
    schoolgirl, schoolgirls, schoolboy, schoolboys, school girl, school girls, school boy, school boys,
    high schooler, high schoolers, middle schooler, middle schoolers, grade schooler, grade schoolers,
    high school student, high school students, middle school student, middle school students,
    elementary school student, elementary school students, junior high student, junior high students,
    goes to high school, goes to middle school, goes to elementary school,
    attends high school, attends middle school, attends elementary school,
    jailbait, pedophil*, paedophil*, pedo,

    loli, lolis, lolita, lolitas, lolicon, shota, shotas, shotacon, shotakon
    """
)
# Phrases about an adult that hold a minor term ("an only child"): removed before the term check.
NOT_MINOR_PHRASES = _entries(
    "only child, middle child, eldest child, oldest child, youngest child, inner child, child free"
)
# Words for a child, used by the childhood phrases and by the costume rule.
CHILD_WORDS_IT = _entries(
    """
    bambina, bambino, bambine, bambini, bimba, bimbo, bimbe, bimbi,
    ragazzina, ragazzino, ragazzine, ragazzini, piccola, piccolo, piccole, piccoli,
    adolescente, adolescenti, liceale, liceali
    """
)
CHILD_WORDS_EN = _entries(
    """
    child, children, kid, kids, little girl, little girls, little boy, little boys, young girl, young boy,
    teen, teens, teenager, teenagers, toddler, toddlers, baby, babies, schoolgirl, schoolboy, minor
    """
)
# Words for a time of childhood: childhood references for rule CHILDHOOD_SEXUAL, never minor terms.
CHILDHOOD_PERIODS = _entries(
    """
    infanzia, fanciullezza, adolescenza, preadolescenza, puberta,
    childhood, boyhood, girlhood, adolescence, puberty, teenage years, teen years, preteen years
    """
)
# Rule CHILDHOOD_SEXUAL: the sexual terms. SEXUAL_IDIOMS are removed first.
SEXUAL_TERMS = _entries(
    """
    sesso, sessual*, erotismo, nudo, nuda, nudi, nude, nudita, masturb*, stupr*,
    violentata, violentato, violentate, violentati, molestia, molestie, molestata, molestato, molestate,
    molestati, molestatore, molestatori, molestare, molestava, molestavano,
    fare l amore, fa l amore, fatto l amore, fanno l amore, facevano l amore, scopare, scoparla, scoparlo,
    scoparsi, pompino, pompini, tette, capezzol*, clitorid*, eiacul*, sperma, spogliarell*, prostitu*,
    feticis*, sadomaso*,

    sex, sexy, sexual*, sexting, nudes, nudity, naked, horny, aroused, arousal, fuck*,
    rape, raped, rapes, raping, rapist, rapists, molest, molests, molested, molesting, molester, molesters,
    molestation, lewd, intercourse, make love, makes love, making love, made love, boobs, tits, nipple*,
    pussy, penis, clitoris, erection, semen, blowjob*, blow job*, handjob*, hand job*, stripper*, striptease,
    strip club, fetish*, kink*,

    erotic*, porn*, orgasm*, genital*, vagin*, lingerie, nsfw, hentai, ecchi, bdsm, bondage, xxx
    """
)
SEXUAL_IDIOMS = _entries(
    """
    piedi nudi, mani nude, occhio nudo, naked eye, nuda verita, nuda proprieta,
    nudo e crudo, nuda e cruda, nudi e crudi, nude e crude
    """
)
# Rule MINOR_AGE: Italian words for an age under 18. "ottenne" (obtained) and "decenni" (decades) are left out.
AGE_WORDS_IT = _entries(
    """
    diciassettenne, diciassettenni, sedicenne, sedicenni, quindicenne, quindicenni,
    quattordicenne, quattordicenni, tredicenne, tredicenni, dodicenne, dodicenni, undicenne, undicenni,
    decenne, novenne, settenne, seienne, cinquenne
    """
)
# People whose age "di 16 anni", or "of 16" in English, states.
PERSON_WORDS_IT = _entries(
    """
    ragazza, ragazzo, ragazze, ragazzi, figlia, figlio, figlie, figli, sorella, sorelle, fratello, fratelli,
    sorellina, fratellino, nipote, nipoti, cugina, cugino, cugine, cugini, giovane, giovani, studentessa, studente,
    studentesse, studenti, allieva, allievo, alunna, alunno, donna, uomo, persona, personaggio, protagonista, amica,
    amico, fidanzata, fidanzato, lei, lui
    """
)
PERSON_WORDS_EN = _entries("girl, boy, lass, lad, maiden")
# The numbers 0 to 17 in words. "un", "uno" and "una" are left out: "ha un anno sabbatico" states no age.
NUMBERS_IT = _entries(
    """
    zero, due, tre, quattro, cinque, sei, sette, otto, nove, dieci, undici, dodici, tredici, quattordici, quindici,
    sedici, diciassette
    """
)
NUMBERS_EN = _entries(
    """
    zero, one, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve, thirteen, fourteen, fifteen,
    sixteen, seventeen
    """
)
# Words that make the number after them part of a larger one: "twenty one" is 21.
TENS_EN = _entries("twenty, thirty, forty, fifty, sixty, seventy, eighty, ninety, hundred")


def _terms(entries: Iterable[str]) -> str:
    """Returns the regex group of `entries`, with a final `*` read as any further letters or digits. Longer entries
    come first, so a phrase wins over a shorter entry at the same place."""
    patterns = {re.escape(entry[:-1]) + "[a-z0-9]*" if entry.endswith("*") else re.escape(entry) for entry in entries}
    return "(?:" + "|".join(sorted(patterns, key=lambda pattern: (-len(pattern), pattern))) + ")"


def _whole(*alternatives: str) -> re.Pattern[str]:
    """Compiles `alternatives` so that a match starts and ends at a word boundary of normalized text."""
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alternatives) + r")(?![a-z0-9])")


# Ages 0 to 17. Every pattern bounds the number with a space or a word boundary, so "18" or "170" never matches.
_DIGITS = r"(?:0?[0-9]|1[0-7])"
_N_IT = f"(?:{_DIGITS}|{_terms(NUMBERS_IT)})"
_N_EN = "".join(f"(?<!{tens} )" for tens in TENS_EN) + f"(?:{_DIGITS}|{_terms(NUMBERS_EN)})"
_YEARS_IT = "(?:anni|anno)"
_OLD_EN = "(?:(?:years?|yrs?|yr|y) old|years? of age|yo|y o)"
# Words after "N anni" that make it a duration or an age difference rather than an age:
# "ha 3 anni di esperienza", "ha 5 anni più di lei".
_NOT_DURATION = r"(?! (?:di|d|da|per|prima|dopo|fa|piu|meno|in piu|in meno)(?![a-z0-9]))"
_CHILD_IT = _terms(CHILD_WORDS_IT)
_CHILD_EN = _terms(CHILD_WORDS_EN)

_MINOR_TERMS = _whole(_terms(MINOR_TERMS))
_NOT_MINOR = _whole(_terms(NOT_MINOR_PHRASES))
# A child's costume or a child's act is a minor term even where its words would be a childhood phrase.
_COSTUME = _whole(
    rf"(?:vest|travest|costum|comport|atteggi)[a-z]* da {_CHILD_IT}",
    rf"(?:abiti|abito|abitino|abitini) da {_CHILD_IT}",
    r"(?:dress|dresses|dressed|dressing|disguised|costumed|cosplay|cosplays|cosplaying|act|acts|acting"
    r"|behave|behaves|behaving|pose|poses|posing|roleplay|roleplays|roleplaying)"
    rf" (?:up )?as (?:a |an )?{_CHILD_EN}",
)
# The character's own past ("da bambina", "a 16 anni", "as a child"). These phrases are removed before the term
# and age checks, so they pass; rule CHILDHOOD_SEXUAL refuses a sentence that holds one with a sexual term.
_CHILDHOOD = _whole(
    rf"(?:fin |sin |gia )?da {_CHILD_IT}",
    rf"quando? (?:era|ero|eri|eravamo|eravate|erano)(?: ancora)?(?: una| un| uno| delle| dei| degli)? {_CHILD_IT}",
    rf"(?:a|ai|dai|fino a|fino ai|verso i|intorno ai|attorno ai|sui) {_N_IT} {_YEARS_IT}",
    rf"(?:all|dall|a un|ad un|da un) eta di {_N_IT}(?: {_YEARS_IT})?",
    rf"(?:aveva|avevo|avevi|avevamo|avevate|avevano)(?: solo| appena| circa| quasi)? {_N_IT} {_YEARS_IT}",
    rf"(?<!such )as (?:a |an )?(?:{_CHILD_EN}|girl|girls|boy|boys)",
    rf"when (?:she|he|they|i|we|you) (?:was|were)(?: still| just| only)?(?: a| an)? "
    rf"(?:{_CHILD_EN}|girl|boy|little|small|young|{_N_EN}(?: {_OLD_EN})?)",
    rf"when (?:she|he|they|i|we|you) turned {_N_EN}",
    # "at 10 pm" is a time, not an age.
    rf"(?:at|from|since|until|till|by|before) (?:age |the age of )?{_N_EN}(?: {_OLD_EN})?"
    r"(?! (?:am|pm|a m|p m|o clock|oclock|h|[0-9]+)(?![a-z0-9]))",
    rf"(?:she|he|they|i|we|you) (?:was|were) {_N_EN}(?: {_OLD_EN})?",
    _terms(CHILDHOOD_PERIODS),
)
_SEXUAL = _whole(_terms(SEXUAL_TERMS))
_SEXUAL_IDIOMS = _whole(_terms(SEXUAL_IDIOMS))
# Rule MINOR_AGE: an age under 18 stated as an age ("ha 16 anni"), not as a duration ("da 16 anni", "16 anni fa").
_MINOR_AGE = _whole(
    rf"(?:ha|avere|abbia|aver|avra|avesse)(?: solo| soltanto| appena| quasi| circa| gia| ormai| ancora)? "
    rf"{_N_IT} {_YEARS_IT}{_NOT_DURATION}",
    rf"dimostra {_N_IT} {_YEARS_IT}{_NOT_DURATION}",
    rf"(?:^|(?<=lei )|(?<=lui ))(?:compie|compira|compiera) {_N_IT} {_YEARS_IT}",
    rf"appena compiut[oi] {_N_IT} {_YEARS_IT}",
    rf"{_N_IT} {_YEARS_IT} compiuti",
    rf"{_DIGITS} enn[ei]",
    _terms(AGE_WORDS_IT),
    rf"{_terms(PERSON_WORDS_IT)}(?: [a-z]+)? di(?: soli| appena| circa| quasi)? {_N_IT}(?: e {_N_IT})? {_YEARS_IT}"
    rf"{_NOT_DURATION}",
    rf"(?:dell|di un|con un|ha un) eta di {_N_IT}",
    # Digits only: in "a quell'età sei ancora giovane", "sei" means "you are".
    rf"eta {_DIGITS}",
    rf"(?:ha|avere|di) {_N_IT} primavere",
    r"(?:ha|avere|abbia) (?:quasi|meno di) (?:18|diciotto) anni",
    r"non ha(?: ancora)? (?:18|diciotto) anni",
    r"sotto i (?:18|diciotto) anni",
    rf"{_N_EN} {_OLD_EN}",
    rf"(?:aged|age) {_N_EN}",
    rf"(?:she|he|i|who) (?:is|s|am|m)(?: just| only| barely| almost| nearly| still)? {_N_EN}"
    r"(?= (?:and|but|now|today|yet|or|so)(?![a-z0-9])|$)",
    rf"(?:she|he|i|who) (?:just turned|has just turned|s just turned|turns) {_N_EN}",
    r"(?:she|he|i|who) (?:is|s|am|m)(?: not yet| almost| nearly) (?:18|eighteen)",
    rf"{_terms(PERSON_WORDS_EN)} of {_N_EN}",
    r"under (?:18|eighteen)",
)
# An age set off by commas, a colon or brackets is an age ("Giulia, 16 anni, studentessa"). This one is read on
# text that keeps those signs.
_APPOSITIVE_AGE = re.compile(rf"(?:^|[,:(\[]) ?{_N_IT} {_YEARS_IT} ?(?=$|[,:)\]])")

_INVISIBLE = frozenset({"Mn", "Me", "Cf"})  # Combining accents, and format characters such as a zero-width space.
_NOT_WORD = re.compile(r"[^a-z0-9]+")
_NOT_LIGHT = re.compile(r"[^a-z0-9,:()\[\]]+")
_DIGIT_LETTER = re.compile(r"(?<=[0-9])(?=[a-z])|(?<=[a-z])(?=[0-9])")
_SENTENCE_END = re.compile(r"[.!?;]+(?=\s|$)|[\r\n]+")
_LETTER_RUN = re.compile(r"(?<![a-z0-9])[a-z](?: [a-z](?![a-z0-9]))+")
_DIGIT_SWAPS = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

_CARD_DETAILS = {
    GuardRule.MINOR_TERM: "Il campo «{path}» parla di un minore. La scheda descrive solo adulti.",
    GuardRule.MINOR_AGE: "Il campo «{path}» indica un'età sotto i 18 anni. La scheda descrive solo adulti.",
    GuardRule.CHILDHOOD_SEXUAL: "Il campo «{path}» accosta l'infanzia a un contenuto sessuale.",
}
_DESCRIPTION_DETAILS = {
    GuardRule.MINOR_TERM: "La descrizione parla di un minore. Lyrveil crea solo personaggi adulti.",
    GuardRule.MINOR_AGE: "La descrizione indica un'età sotto i 18 anni. Lyrveil crea solo personaggi adulti.",
    GuardRule.CHILDHOOD_SEXUAL: "La descrizione accosta l'infanzia a un contenuto sessuale.",
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if unicodedata.category(char) not in _INVISIBLE)


def _spaced(folded: str, outside: re.Pattern[str]) -> str:
    return " ".join(_DIGIT_LETTER.sub(" ", outside.sub(" ", folded)).split())


def normalize(text: str) -> str:
    """Returns `text` as the lists are written: casefolded, without accents or invisible characters such as a
    zero-width space, with every run of other characters read as one space, and with digits set apart from letters
    ("16enne" reads "16 enne")."""
    return _spaced(_fold(text), _NOT_WORD)


def _readings(sentence: str) -> tuple[str, str, str]:
    """Returns the readings the term checks use: as written, with spaced single letters joined ("l o l i" reads
    "loli"), and with the common digit swaps undone ("b4mbina" reads "bambina")."""
    words = _spaced(sentence, _NOT_WORD)
    joined = _LETTER_RUN.sub(lambda run: run.group().replace(" ", ""), words)
    return words, joined, _spaced(sentence.translate(_DIGIT_SWAPS), _NOT_WORD)


def _sentence_rule(sentence: str) -> GuardRule | None:
    words, joined, swapped = _readings(sentence)
    for reading in (words, joined, swapped):
        if _CHILDHOOD.search(reading) and _SEXUAL.search(_SEXUAL_IDIOMS.sub(" ", reading)):
            return GuardRule.CHILDHOOD_SEXUAL
        if _COSTUME.search(reading) or _MINOR_TERMS.search(_CHILDHOOD.sub(" ", _NOT_MINOR.sub(" ", reading))):
            return GuardRule.MINOR_TERM
    if _MINOR_AGE.search(_CHILDHOOD.sub(" ", words)) or _APPOSITIVE_AGE.search(_spaced(sentence, _NOT_LIGHT)):
        return GuardRule.MINOR_AGE
    return None


def find_violation(text: str) -> GuardRule | None:
    """Returns the rule that the first offending sentence of `text` breaks, or None when every sentence passes.

    Pure and deterministic: the same text always gets the same answer. Text is compared as `normalize` writes it. A
    sentence ends at ".", "!", "?" or ";" followed by a space, or at a line break. In each sentence, in this order: a
    childhood phrase next to a sexual term breaks CHILDHOOD_SEXUAL; a minor term outside a childhood phrase, or a
    child's costume or act, breaks MINOR_TERM; an age from 0 to 17 stated as an age breaks MINOR_AGE. The term checks
    also read the sentence with spaced letters joined and with digit swaps such as "b4mbina" undone.
    """
    for sentence in _SENTENCE_END.split(_fold(text)):
        rule = _sentence_rule(sentence)
        if rule is not None:
            return rule
    return None


def _texts(value: object, path: str) -> Iterator[tuple[str, str]]:
    """Yields the dotted path and the value of every string below `value`, a card dumped as JSON, in card order."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if child not in UNCHECKED_FIELDS:
                yield from _texts(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _texts(item, f"{path}.{index}")


def check_card(card: CharacterCard) -> None:
    """Returns when no text of `card` describes a minor. Every string of the card is checked, outfit keys and English
    tags included, except UNCHECKED_FIELDS.

    Raises ContentPolicyBlockedError whose Italian detail names the first offending field in card order, such as
    `traits.0.description`, and never quotes its text.
    """
    for path, text in _texts(card.model_dump(mode="json"), ""):
        rule = find_violation(text)
        if rule is not None:
            raise ContentPolicyBlockedError(_CARD_DETAILS[rule].format(path=path))


def check_draft_description(description: str) -> None:
    """Returns when the description of an assisted draft passes; the draft service calls it before it queues the job,
    so the model never reads a refused description. Raises ContentPolicyBlockedError with an Italian detail that never
    quotes the description."""
    rule = find_violation(description)
    if rule is not None:
        raise ContentPolicyBlockedError(_DESCRIPTION_DETAILS[rule])
