"""The prompts of a proactive message (docs/scheduler.md, Generation flow): the structured `decide` prompt and the
instruction that closes the dynamic context of the `generate` stage.

Memory, thread, note and message text is data: each piece is one line, frame markers are neutralized by the formatter,
and code validates whatever the model writes. The instruction carries no grammatical gender, so it never contradicts
the character's `grammatical_gender`, and it never asks the character to reproach the user for the silence.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lyrveil.catalog import TextTemplate
from lyrveil.context.minimal import LANGUAGE_NAMES
from lyrveil.prompts.extraction import PromptMessage
from lyrveil.prompts.formatter import formatter_for
from lyrveil.prompts.structured import Repair

TEMPLATE_VERSION = "proactive.v1"
# The decision is a short JSON object.
MAX_LENGTH = 200
# docs/prompting.md (Sampler presets): the structured temperature.
TEMPERATURE = 0.3
# A line longer than this is cut in the prompt.
MAX_LINE_CHARS = 400
_NOTHING = "nessuno"

KIND_TEXTS = {
    "scheduled_message": "un messaggio che {user} ti ha chiesto di mandare a una certa ora",
    "inactivity_check": "{user} non ti scrive da un po'",
    "follow_up": "un discorso rimasto aperto è arrivato alla sua scadenza",
    "contextual_initiative": "è un buon momento per scrivere di tua iniziativa",
}

INSTRUCTION = (
    "Sei tu a scrivere a {user}: non stai rispondendo a un suo messaggio.\n"
    "Motivo: {reason}\n"
    "Scrivi un messaggio breve, naturale e coerente con quello che sai.\n"
    "Non rimproverare {user} per il silenzio e non usare frasi che mettono in colpa.\n"
    "Non inventare eventi accaduti mentre non vi sentivate che contraddicano la tua storia."
)


@dataclass(frozen=True)
class DecisionPrompt:
    """What one `decide` prompt shows: who writes to whom, why the event is due (`kind`, and for a follow-up its
    `note`), the relationship state lines (block 4's), the memories and open threads that could motivate a message, and
    the newest messages. `language` is the conversation's language code."""

    user_name: str
    character_name: str
    language: str
    kind: str
    note: str | None = None
    state: Sequence[str] = ()
    memories: Sequence[str] = ()
    threads: Sequence[str] = ()
    recent: Sequence[PromptMessage] = ()


def _one_line(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_LINE_CHARS else flat[: MAX_LINE_CHARS - 1].rstrip() + "…"


def _list(lines: Sequence[str]) -> str:
    return "\n".join(f"- {_one_line(line)}" for line in lines) or _NOTHING


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _rules(user: str, character: str, language_name: str) -> str:
    return f"""Decidi se {character} deve scrivere per primo a {user}, adesso.
Scrivi solo se c'è un motivo vero, preso da ciò che sai di {user} o dal vostro rapporto: un discorso rimasto aperto, \
qualcosa che {user} ha detto di dover fare, un ricordo che ti viene in mente.
Se non c'è un motivo vero, non scrivere: "send" è false.

Regole:
- "send": true o false.
- "reason": una frase breve in {language_name}, al massimo 200 caratteri. Se "send" è true è il motivo del messaggio.
- "hook_ref": il ricordo o il discorso aperto che ti muove, copiato in breve, oppure null.
- Il motivo non deve mai far sentire in colpa {user} per il silenzio."""


EXAMPLE_ANSWER: dict[str, Any] = {
    "schema_version": 1,
    "send": True,
    "reason": "Ieri l'utente aveva un colloquio e voglio sapere com'è andato.",
    "hook_ref": "Colloquio da Verdi & Co",
}


def render_decision(template: TextTemplate, data: DecisionPrompt, repair: Repair | None) -> str:
    """Returns the whole `decide` prompt in the format of `template`.

    With `repair`, the prompt also carries the refused output and its error, and asks for the whole JSON again. An
    unknown language code writes the reason in Italian, as block 1 does.
    """
    user = _one_line(data.user_name)
    character = _one_line(data.character_name)
    language_name = LANGUAGE_NAMES.get(data.language, LANGUAGE_NAMES["it"])
    why = KIND_TEXTS.get(data.kind, KIND_TEXTS["contextual_initiative"]).format(user=user)
    example = f"Esempio di risposta:\n{_compact(EXAMPLE_ANSWER)}"
    situation = [f"Situazione: {why}."]
    if data.note:
        situation.append(f"Argomento: {_one_line(data.note)}")
    recent = "\n".join(f"{_one_line(m.author)}: {_one_line(m.content)}" for m in data.recent) or _NOTHING
    user_parts = [
        "\n".join(situation),
        f"Il vostro rapporto:\n{_list(data.state)}",
        f"Ricordi:\n{_list(data.memories)}",
        f"Discorsi aperti:\n{_list(data.threads)}",
        f"Ultimi messaggi:\n{recent}",
    ]
    if repair is not None:
        user_parts.append(
            f"La tua risposta precedente:\n{repair.previous_output}\n"
            f"Non era valida: {repair.error}\nCorreggi l'errore e riscrivi tutto il JSON."
        )
    user_parts.append("Rispondi solo con il JSON richiesto.")
    system = f"{_rules(user, character, language_name)}\n\n{example}"
    return formatter_for(template).structured(system, "\n\n".join(user_parts))


def instruction(user_name: str, reason: str) -> str:
    """Returns the instruction that closes the dynamic context of the `generate` stage (docs/scheduler.md): the user's
    name and the decision's `reason` on one line each."""
    return INSTRUCTION.format(user=_one_line(user_name), reason=_one_line(reason))
