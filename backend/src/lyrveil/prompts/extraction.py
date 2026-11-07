"""The prompt of the memory extraction task (docs/prompting.md, Extraction template).

It follows `extraction.v2.j2` of the document, as Python because the backend has no template engine yet: a role
sentence, the rules, one worked example, then the data (existing memories as E1..En, recent episodes as P1..Pn, the
context messages and the window) and "Rispondi solo con il JSON richiesto". Message text is data: each message is one
line, frame markers are neutralized by the formatter, and code validates whatever the model writes.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lyrveil.catalog import TextTemplate
from lyrveil.context.minimal import LANGUAGE_NAMES
from lyrveil.prompts.formatter import formatter_for
from lyrveil.prompts.structured import Repair

# v2 labels memories in conflict `[incerto]` and tells the model what the label means.
TEMPLATE_VERSION = "extraction.v2"
UNCERTAIN_LABEL = "[incerto]"
# docs/prompting.md (Structured tasks): the `max_length` of memory extraction.
MAX_LENGTH = 1200
# docs/prompting.md (Sampler presets): the structured temperature.
TEMPERATURE = 0.3
# A message longer than this is cut in the prompt, so that 12 long messages cannot push the rules out of the context.
MAX_MESSAGE_CHARS = 1200
NOTHING = "nessuno"


@dataclass(frozen=True)
class PromptMessage:
    """A message as the prompt shows it: `[seq] author: text`."""

    seq: int
    author: str
    content: str


@dataclass(frozen=True)
class Labeled:
    """An existing memory or recent episode with the label the model refers to it by (`E1`, `P1`). `uncertain` marks a
    memory of an unresolved conflict, shown after UNCERTAIN_LABEL."""

    ref: str
    text: str
    uncertain: bool = False


@dataclass(frozen=True)
class ExtractionPrompt:
    """Everything one extraction prompt shows. `language` is the conversation's language code."""

    user_name: str
    character_name: str
    language: str
    existing: Sequence[Labeled]
    episodes: Sequence[Labeled]
    context: Sequence[PromptMessage]
    window: Sequence[PromptMessage]


def _rules(user: str, character: str, language_name: str) -> str:
    return f"""Analizzi una chat tra {user} e il personaggio {character}.
Estrai solo ciò che sarà utile ricordare fra settimane. Ignora saluti e chiacchiere senza conseguenze.

Regole:
- "stated": {user} lo dice chiaramente. "inferred": lo deduci tu. "fictional": è un fatto della storia inventata.
- Non trasformare mai una deduzione in un fatto.
- "temporary" solo per cose che smetteranno di valere: indica "valid_until" se puoi.
- Confronta con i ricordi esistenti (E1, E2...). "duplicate": è già noto. "update": il fatto è cambiato.
  "contradiction": non si capisce quale sia vero. "correction": {user} corregge esplicitamente un errore.
- {UNCERTAIN_LABEL} segna ricordi in conflitto: usa "update" o "correction" solo se i messaggi chiariscono quale vale.
- Se qualcosa sembra cambiare la scheda del personaggio, usa il tipo "identity_candidate": è solo una proposta.
- "subject" è una chiave breve come user.employer, user.city, user.likes. Omettila se non è adatta.
- Cita sempre i numeri dei messaggi che lo provano in "source_seqs".
- Scrivi i contenuti in {language_name}, in terza persona, in una frase breve.
- Registra un episodio solo per eventi davvero importanti.
- Per il rapporto registra solo cambiamenti con un motivo chiaro."""


# The worked example: an original exchange, short enough to cost few tokens. A unit test validates the answer against
# ExtractionResult and the grammar.
EXAMPLE_INPUT = """Ricordi esistenti:
E1: Lavora allo Studio Rossi.
Episodi recenti:
nessuno
Contesto, da non analizzare:
[40] Dario: Com'è andata la settimana?
Messaggi da analizzare:
[41] Utente: Ho cambiato lavoro, da lunedì sono da Verdi & Co.
[42] Dario: Davvero? Allora stasera si festeggia."""
EXAMPLE_ANSWER: dict[str, Any] = {
    "schema_version": 1,
    "memories": [
        {
            "relation": "update",
            "target_ref": "E1",
            "kind": "user_fact",
            "content": "Lavora da Verdi & Co.",
            "subject": "user.employer",
            "value": "Verdi & Co",
            "provenance": "stated",
            "durability": "lasting",
            "valid_until": None,
            "importance": 4,
            "confidence": "high",
            "source_seqs": [41],
        }
    ],
    "episodes": [],
    "relationship_events": [],
    "state": {
        "emotional_state": None,
        "current_topic": "il nuovo lavoro",
        "open_threads_add": [],
        "open_threads_close": [],
        "goals": [],
        "narrative_change": None,
    },
}


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _one_line(text: str) -> str:
    """Returns `text` on one line, cut to MAX_MESSAGE_CHARS: a line break inside a message could otherwise start a line
    that looks like another numbered message."""
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_MESSAGE_CHARS else flat[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"


def _labeled(items: Sequence[Labeled]) -> str:
    lines = (f"{item.ref}: {UNCERTAIN_LABEL + ' ' if item.uncertain else ''}{_one_line(item.text)}" for item in items)
    return "\n".join(lines) or NOTHING


def _messages(messages: Sequence[PromptMessage]) -> str:
    return "\n".join(f"[{m.seq}] {_one_line(m.author)}: {_one_line(m.content)}" for m in messages) or NOTHING


def render_prompt(template: TextTemplate, data: ExtractionPrompt, repair: Repair | None) -> str:
    """Returns the whole prompt in the format of `template`.

    With `repair`, the prompt also carries the refused output and its error, and asks for the whole JSON again. An
    unknown language code writes the contents in Italian, as block 1 does.
    """
    language_name = LANGUAGE_NAMES.get(data.language, LANGUAGE_NAMES["it"])
    rules = _rules(_one_line(data.user_name), _one_line(data.character_name), language_name)
    example = f"Esempio.\n{EXAMPLE_INPUT}\nRisposta:\n{_compact(EXAMPLE_ANSWER)}"
    user_parts = [
        f"Ricordi esistenti:\n{_labeled(data.existing)}\n"
        f"Episodi recenti:\n{_labeled(data.episodes)}\n"
        f"Contesto, da non analizzare:\n{_messages(data.context)}\n"
        f"Messaggi da analizzare:\n{_messages(data.window)}"
    ]
    if repair is not None:
        user_parts.append(
            f"La tua risposta precedente:\n{repair.previous_output}\n"
            f"Non era valida: {repair.error}\nCorreggi l'errore e riscrivi tutto il JSON."
        )
    user_parts.append("Rispondi solo con il JSON richiesto.")
    return formatter_for(template).structured(f"{rules}\n\n{example}", "\n\n".join(user_parts))
