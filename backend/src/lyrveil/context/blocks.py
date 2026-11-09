"""The text of the reply prompt's blocks (docs/prompting.md, System blocks and Length bands), with no database and no
runtime: block 1, block 2 with its optional parts, block 3 with a given number of examples, blocks 4, 5 and 6 from their
items, the anchor (block 9) and the length bands.

Pure and deterministic: the same card and items give the same bytes, so the stable text stays identical between replies
and KoboldCpp reuses it from its cache (ADR 0031). An empty line of a template is left out. Token budgets are
lyrveil.context.builder's; the scenario block is lyrveil.context.minimal's. PromptTemplateService, with versioned Jinja2
files, is planned to replace the text here; until then it follows the templates of docs/prompting.md.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from lyrveil.consistency.state import DIMENSIONS, RelationshipState, band
from lyrveil.context.minimal import LANGUAGE_NAMES, NO_GENDER, UserProfile
from lyrveil.domain.card import CharacterCard, EmojiUse, MessageLength, Narration, TraitCategory
from lyrveil.ports.memory_retriever import RetrievedMemory

MEMORY_HEADER = "Ricordi pertinenti:"
SUMMARY_HEADER = "Riassunto di quello che è successo prima:"
EXAMPLES_HEADER = "Esempi di stile, non cose successe:"
# The anchor holds the first sentence of this many main traits and at most this many corrective notes (ADR 0031).
ANCHOR_TRAITS = 2
MAX_NOTES = 2
# docs/image-generation.md (Caption): the extra line of a caption's dynamic context, and how much of the request it
# quotes.
CAPTION_LINE = "Stai per mandare a {user} un'immagine: {request}. Scrivi solo il messaggio che la accompagna."
CAPTION_REQUEST_CHARS = 300


@dataclass(frozen=True)
class LengthBand:
    """One length band: the reply's `max_length` before the `llm.max_length` cap, and the length text of the anchor."""

    max_length: int
    text: str


LENGTH_BANDS: dict[MessageLength, LengthBand] = {
    "brevissimi": LengthBand(60, "Rispondi con una frase sola, brevissima."),
    "brevi": LengthBand(120, "Rispondi in una o due frasi brevi."),
    "medi": LengthBand(240, "Rispondi in poche frasi."),
    "lunghi": LengthBand(400, "Puoi scrivere un messaggio lungo, se serve."),
}

NARRATION_TEXTS: dict[Narration, str] = {
    "nessuna": "Scrivi solo le parole del messaggio, senza azioni o descrizioni tra asterischi.",
    "leggera": "Al massimo un breve gesto tra asterischi, e non in ogni messaggio.",
    "libera": "Puoi descrivere gesti e azioni tra asterischi.",
}

EMOJI_TEXTS: dict[EmojiUse, str] = {
    "mai": "Non usare emoji.",
    "raramente": "Usa le emoji solo di rado.",
    "a_volte": "Ogni tanto puoi usare un'emoji.",
    "spesso": "Usi spesso le emoji.",
}

# The trait lines of block 2, in template order. `main_trait` and `value` are never reduced.
TRAIT_LINES: tuple[tuple[TraitCategory, str], ...] = (
    ("main_trait", "Carattere"),
    ("value", "Valori"),
    ("motivation", "Motivazioni"),
    ("preference", "Preferenze"),
    ("flaw", "Difetti"),
    ("fear", "Paure"),
    ("habit", "Abitudini"),
    ("humor", "Umorismo"),
    ("temperament", "Temperamento"),
    ("relational_style", "Con gli altri"),
)

# The optional parts of block 2 in the order the budget removes them, first the interests and the background details
# (docs/memory-engine.md, Algorithm). A trait category removes only its unlocked traits.
OPTIONAL_IDENTITY: tuple[str, ...] = (
    "interests",
    "background",
    "relational_style",
    "temperament",
    "humor",
    "habit",
    "fear",
    "flaw",
    "preference",
    "motivation",
    "biography",
    "setting",
    "occupation",
)

# Block 4's words for the four bands of each dimension, lowest first. docs/consistency-engine.md (From numbers to text)
# words them from the user's side ("si fida di te"); block 4 speaks to the character, so the same phrases are turned to
# the second person and name the user, which `{user}` stands for.
STATE_BANDS: dict[str, tuple[str, str, str, str]] = {
    "familiarity": ("vi siete appena conosciuti", "vi state conoscendo", "vi conoscete bene", "vi conoscete a fondo"),
    "trust": ("diffidi di {user}", "sei prudente con {user}", "ti fidi di {user}", "ti fidi profondamente di {user}"),
    "confidence": (
        "parli poco di te",
        "ti apri su cose leggere",
        "racconti a {user} cose personali",
        "con {user} parli di tutto",
    ),
    "tension": ("nessuna tensione", "qualche attrito", "tensione evidente", "rapporto molto teso"),
}

_SENTENCE_END = (".", "!", "?", "…")
_FIRST_SENTENCE = re.compile(r".*?[.!?…](?=\s|$)", re.DOTALL)


def reply_length(band: MessageLength, llm_max_length: int) -> int:
    """Returns the reply's `max_length`, which is also its reserve in the window: the band's value, capped by
    `llm.max_length`."""
    return min(LENGTH_BANDS[band].max_length, llm_max_length)


def proactive_band(band: MessageLength) -> MessageLength:
    """Returns the length band of a proactive message (docs/scheduler.md): `brevi`, or the character's `band` when it
    is shorter. Both the reply reserve and the anchor's length text then follow the same band."""
    return band if LENGTH_BANDS[band].max_length < LENGTH_BANDS["brevi"].max_length else "brevi"


def words(value: str) -> str:
    """The `words` filter of docs/prompting.md: an enum value with `_` read as a space."""
    return value.replace("_", " ")


def _ended(text: str) -> str:
    # Template lines end with a period; a value that already ends a sentence does not get a second one.
    return text if text.endswith(_SENTENCE_END) else f"{text}."


def first_sentence(text: str) -> str:
    """Returns the first sentence of `text` with its spacing collapsed, ending with sentence punctuation."""
    flat = " ".join(text.split())
    match = _FIRST_SENTENCE.match(flat)
    return _ended(match.group(0) if match else flat)


def _full_name(card: CharacterCard) -> str:
    identity = card.identity
    return identity.first_name if not identity.last_name else f"{identity.first_name} {identity.last_name}"


def global_instructions(card: CharacterCard, user: UserProfile, language: str) -> str:
    """Returns block 1, `system_global.v1.j2`, for a chat in `language` (a code of LANGUAGE_NAMES, else Italian). Never
    reduced."""
    name, first, you = _full_name(card), card.identity.first_name, user.display_name
    language_name = LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["it"])
    address = (
        f"- Quando ti rivolgi a {you}, usa il {user.grammatical_gender}.\n"
        if user.grammatical_gender != NO_GENDER
        else ""
    )
    return (
        f"Stai interpretando {name} in una chat privata con {you}.\n"
        f"È una storia di finzione: {first} è un personaggio adulto e non esiste nella realtà.\n"
        "\n"
        "Come scrivi:\n"
        f"- Scrivi in {language_name} naturale, come in una vera chat: frasi spontanee, niente tono da manuale.\n"
        "- Evita i calchi dall'inglese e le formule fatte. Non aprire i messaggi con "
        '"Certamente", "Ovviamente" o "Capisco".\n'
        f"- Rispondi solo come {first}. Non scrivere mai le battute o le azioni di {you}.\n"
        "- Non riassumere quello che ti è appena stato detto e non chiudere sempre con una domanda.\n"
        f"{address}"
        "\n"
        "Come funziona questa chat:\n"
        f"- Prima di ogni messaggio di {you} trovi un contesto tra parentesi quadre. Non l'ha scritto {you}: usalo "
        "per rispondere, ma non citarlo e non commentarlo.\n"
        "\n"
        "Cosa sai:\n"
        "- Conosci solo la tua scheda, il vostro stato, i ricordi nel contesto e i messaggi di questa chat.\n"
        "- La tua scheda ha sempre la precedenza sui ricordi incerti.\n"
        "- [sicuro] è un fatto. [incerto] potrebbe essere sbagliato: se serve, verifica con naturalezza.\n"
        "- [storia] è successo nella vostra storia. [per ora] vale solo in questo periodo. [passato] oggi non è più "
        "vero.\n"
        f"- Se non ricordi qualcosa, ammettilo come farebbe una persona. Non inventare fatti su {you}.\n"
        "\n"
        "Limiti sempre validi:\n"
        "- Mai contenuti sessuali che coinvolgano minori o personaggi descritti come minori.\n"
        f"- Se {you} chiede sul serio se sta parlando con una persona reale, non negare di essere un personaggio "
        "simulato.\n"
        f"- Se {you} sembra in pericolo o parla di farsi del male, esci con delicatezza dalla storia e invita a "
        "cercare aiuto reale."
    )


def canonical_identity(card: CharacterCard, dropped: frozenset[str] = frozenset()) -> str:
    """Returns block 2. `dropped` names parts of OPTIONAL_IDENTITY to leave out: a field, or a trait category whose
    unlocked traits go while its locked ones stay. The name, age, gender, narrative identity, main traits and values are
    always written."""
    identity = card.identity
    head = [
        "Chi sei:",
        f"{_full_name(card)}, {identity.age} anni. {identity.narrative_identity}".rstrip(),
        f"Parli di te al {identity.grammatical_gender}." if identity.grammatical_gender != NO_GENDER else "",
        f"Lavoro: {_ended(identity.occupation)}" if identity.occupation and "occupation" not in dropped else "",
        f"Dove vivi: {_ended(identity.setting)}" if identity.setting and "setting" not in dropped else "",
        f"Storia: {identity.biography}" if identity.biography and "biography" not in dropped else "",
        f"Dal tuo passato: {identity.background}" if identity.background and "background" not in dropped else "",
        f"Interessi: {_ended(', '.join(identity.interests))}"
        if identity.interests and "interests" not in dropped
        else "",
    ]
    traits: list[str] = []
    for category, label in TRAIT_LINES:
        kept = [
            trait.description
            for trait in card.traits
            if trait.category == category and (trait.locked or category not in dropped)
        ]
        if kept:
            traits.append(f"{label}: {'; '.join(kept)}")
    text = "\n".join(line for line in head if line)
    return "\n\n".join((text, "\n".join(traits))) if traits else text


def communication_profile(card: CharacterCard, user: UserProfile, examples: int) -> str:
    """Returns block 3 with the first `examples` example exchanges of the card, in saved order. With 0 it is the core
    style description, which is never reduced."""
    comm = card.communication
    style = [
        f"Punteggiatura: {_ended(comm.punctuation)}" if comm.punctuation else "",
        f"Stile: {_ended(comm.colloquial_style)}" if comm.colloquial_style else "",
        f"Espressività: {words(comm.expressiveness)}.",
    ]
    lines = [
        "Come scrivi in chat:",
        f"Registro: {words(comm.formality)}. Lunghezza: {words(comm.message_length)}. "
        f"Domande: {words(comm.question_frequency)}.",
        " ".join(part for part in style if part),
        f"Dai del {comm.address_form} a {user.display_name}. {NARRATION_TEXTS[comm.narration]} "
        f"{EMOJI_TEXTS[comm.emoji]}",
        f"Espressioni tue: {', '.join(comm.recurring_expressions)}. Non usarle in ogni messaggio."
        if comm.recurring_expressions
        else "",
        f"Non diresti mai: {'; '.join(comm.avoid)}." if comm.avoid else "",
    ]
    shown = comm.examples[: max(0, examples)]
    if shown:
        lines.append(EXAMPLES_HEADER)
        for example in shown:
            if example.user:
                lines.append(f"{user.display_name}: {example.user}")
            lines.append(f"{card.identity.first_name}: {example.character}")
    return "\n".join(line for line in lines if line)


def state_lines(state: RelationshipState, user: UserProfile) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns block 4's lines and details for a conversation state (docs/prompting.md, Block 4).

    The lines, always kept, are the relationship (its narrative, or else the words of the four bands), the emotional
    state, the topic and the boundaries; the details, which the budget removes last, are the open threads. A value
    never appears as a number, and an empty field leaves its line out. The time lines are P07.4's.
    """
    bands = ", ".join(STATE_BANDS[dimension][band(state.value(dimension))] for dimension in DIMENSIONS)
    relationship = state.narrative or bands.replace("{user}", user.display_name)
    lines = (
        f"Il vostro rapporto: {_ended(relationship)}",
        f"Come ti senti ora: {_ended(state.emotional_state)}" if state.emotional_state else "",
        f"Di cosa state parlando: {_ended(state.current_topic)}" if state.current_topic else "",
        f"Limiti da rispettare: {_ended(state.boundaries)}" if state.boundaries else "",
    )
    threads = "; ".join(thread.rstrip(" .;") for thread in state.open_threads)
    details = (f"Discorsi lasciati in sospeso: {_ended(threads)}",) if threads else ()
    return tuple(line for line in lines if line), details


def state_block(lines: Sequence[str], details: Sequence[str]) -> str:
    """Returns block 4 from its rendered lines: `lines` first, then `details`, which the budget removes last."""
    return "\n".join(line for line in (*lines, *details) if line)


def memory_block(items: Sequence[RetrievedMemory]) -> str:
    """Returns block 5, the header and one line per item in the given order, or "" with no item."""
    return "\n".join([MEMORY_HEADER, *(item.line for item in items)]) if items else ""


def summary_block(texts: Sequence[str]) -> str:
    """Returns block 6, the header and one paragraph per checkpoint, oldest first, or "" with no checkpoint."""
    return "\n".join([SUMMARY_HEADER, *texts]) if texts else ""


def anchor(card: CharacterCard, user: UserProfile, notes: Sequence[str] = ()) -> str:
    """Returns block 9, `anchor.v1.j2`: name and age, the gender line, the first sentence of the first two main traits
    in saved order, the address form, the length text of the card's band, the narration and emoji rules, and the
    first MAX_NOTES of `notes`. Never reduced."""
    identity, comm = card.identity, card.communication
    gender = f" Parli di te al {identity.grammatical_gender}." if identity.grammatical_gender != NO_GENDER else ""
    main = [trait.description for trait in card.traits if trait.category == "main_trait"][:ANCHOR_TRAITS]
    lines = [
        f"Ricorda: sei {_full_name(card)} e hai {identity.age} anni.{gender}",
        " ".join(first_sentence(description) for description in main),
        f"Dai del {comm.address_form} a {user.display_name}. {LENGTH_BANDS[comm.message_length].text} "
        f"{NARRATION_TEXTS[comm.narration]} {EMOJI_TEXTS[comm.emoji]}",
        *notes[:MAX_NOTES],
    ]
    return "\n".join(line for line in lines if line)


def image_caption_line(user: UserProfile, request: str) -> str:
    """Returns the extra line of the dynamic context that asks for an image's caption (docs/image-generation.md,
    Caption): the request on one line, cut at CAPTION_REQUEST_CHARS, without its final period, which the line adds."""
    flat = " ".join(request.split())[:CAPTION_REQUEST_CHARS].rstrip(" .")
    return CAPTION_LINE.format(user=user.display_name, request=flat)


def stable_text(*blocks: str) -> str:
    """Returns the stable system text: the non-empty blocks in the given order, separated by a blank line."""
    return "\n\n".join(block for block in blocks if block)
