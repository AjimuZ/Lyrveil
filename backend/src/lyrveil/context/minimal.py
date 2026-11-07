"""The P02 reply context: blocks 1 and 2 of docs/prompting.md as the stable text, and the chat as plain turns.

Pure and deterministic, so two replies of one chat share the stable text byte for byte (ADR 0031). Planned parts are
left out: block 3 and the traits of block 2 need the full card (P03.1), blocks 4, 5 and 9 need state, memories and
the anchor (P04), and the token budget with summaries is ContextBuilder's (P04.5). PromptTemplateService, with its
versioned Jinja2 files, replaces the text below in P04; until then it follows `system_global.v1.j2` and block 2 of
docs/prompting.md.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from lyrveil.domain.characters import CharacterProfile
from lyrveil.domain.messages import Author, Message
from lyrveil.prompts.formatter import ChatParts, Turn

# docs/prompting.md (Language resolution): the supported codes, by the name block 1 writes.
LANGUAGE_NAMES = {"it": "italiano", "en": "inglese", "fr": "francese", "de": "tedesco", "es": "spagnolo"}
NO_GENDER = "non_indicato"


@dataclass(frozen=True)
class UserProfile:
    """The user as the prompt names them: the settings `user.display_name` and `user.grammatical_gender`."""

    display_name: str = "Utente"
    grammatical_gender: str = NO_GENDER


def global_instructions(character: CharacterProfile, user: UserProfile, language: str) -> str:
    """Returns block 1, the global instructions, for a chat in `language` (a code of LANGUAGE_NAMES, else Italian)."""
    name, first, you = character.full_name, character.first_name, user.display_name
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


def canonical_identity(character: CharacterProfile) -> str:
    """Returns block 2 with the card fields P02 stores; a line whose field is empty is left out."""
    lines = [
        "Chi sei:",
        f"{character.full_name}, {character.age} anni. {character.narrative_identity}".rstrip(),
        f"Parli di te al {character.grammatical_gender}." if character.grammatical_gender != NO_GENDER else "",
        f"Lavoro: {character.occupation}." if character.occupation else "",
        f"Dove vivi: {character.setting}." if character.setting else "",
        f"Storia: {character.biography}" if character.biography else "",
    ]
    return "\n".join(line for line in lines if line)


def stable_text(character: CharacterProfile, user: UserProfile, language: str) -> str:
    """Returns the stable system text: block 1, a blank line, then block 2."""
    return f"{global_instructions(character, user, language)}\n\n{canonical_identity(character)}"


def split_current_message(messages: Sequence[Message]) -> tuple[list[Turn], str] | None:
    """Returns the history turns, oldest first, and the text of the user message the reply answers.

    `messages` are the chat's messages before the reply, oldest first. The newest user message among them is the
    current one, and the messages before it are the history; character messages after it, which only a deleted user
    message can leave, are dropped. System messages and empty texts never become turns. Returns None when no user
    message has text.
    """
    turns = [
        Turn("user" if message.author is Author.USER else "character", message.content)
        for message in messages
        if message.author is not Author.SYSTEM and message.content
    ]
    for index in range(len(turns) - 1, -1, -1):
        if turns[index].role == "user":
            return turns[:index], turns[index].text
    return None


def chat_parts(character: CharacterProfile, user: UserProfile, language: str, history: Sequence[Turn]) -> ChatParts:
    """Returns the ChatParts of a P02 reply: the stable text and `history`, with empty dynamic blocks."""
    return ChatParts(
        stable_text=stable_text(character, user, language),
        history=tuple(history),
        state="",
        memories="",
        anchor="",
        character_first_name=character.first_name,
        user_display_name=user.display_name,
    )
