"""The prompts of the summary checkpoint task (docs/prompting.md, Structured tasks): one for a range of messages
(level 0) and one for a rollup of earlier checkpoints.

Both follow the shape of the extraction prompt: a role sentence, the rules, one worked example, then the data and
"Rispondi solo con il JSON richiesto". Message and checkpoint text is data: each message is one line, frame markers are
neutralized by the formatter, and code validates whatever the model writes.
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

TEMPLATE_VERSION = "summary.v1"
# docs/prompting.md (Structured tasks): the `max_length` of a summary checkpoint.
MAX_LENGTH = 700
# docs/prompting.md (Sampler presets): the structured temperature.
TEMPERATURE = 0.3
# A message longer than this is cut in the prompt, so that 40 long messages cannot push the rules out of the context.
MAX_MESSAGE_CHARS = 800
_NOTHING = "nessuno"


@dataclass(frozen=True)
class RolledCheckpoint:
    """An earlier checkpoint a rollup merges: its range, prose and lists (`events`, `decisions`,
    `relationship_changes`, `unresolved`, `key_facts`)."""

    from_seq: int
    to_seq: int
    text: str
    lists: dict[str, list[str]]


@dataclass(frozen=True)
class SummaryPrompt:
    """What one summary prompt shows: `messages` for a level-0 range, or `parts` for a rollup (exactly one of the two is
    not empty). `language` is the conversation's language code."""

    user_name: str
    character_name: str
    language: str
    messages: Sequence[PromptMessage] = ()
    parts: Sequence[RolledCheckpoint] = ()


def _rules(user: str, character: str, language_name: str, rollup: bool) -> str:
    source = "i riassunti di parti successive di una chat" if rollup else "una parte di una chat"
    cited = "dei messaggi che i riassunti citano" if rollup else "dei messaggi che lo provano"
    return f"""Riassumi {source} tra {user} e il personaggio {character}.
Scrivi per chi dovrà riprendere la storia fra settimane: conta ciò che è successo e ciò che resta aperto.

Regole:
- "text": un breve racconto continuo, in terza persona, al passato, al massimo 900 caratteri.
- Gli elenchi hanno al massimo 3 voci brevi ciascuno e possono essere vuoti.
- "events": cosa è successo. "decisions": cosa è stato deciso o promesso.
- "relationship_changes": come è cambiato il rapporto. "unresolved": cosa resta in sospeso.
- "key_facts": fatti da non dimenticare su {user} o su {character}.
- Non aggiungere nulla che non sia nel testo. Non trasformare una deduzione in un fatto.
- "source_seqs": i numeri {cited}, al massimo 24.
- Scrivi in {language_name}."""


# The worked example: an original exchange, short enough to cost few tokens. A unit test validates the answer against
# SummaryResult and the grammar.
EXAMPLE_INPUT = """Messaggi da riassumere:
[3] Utente: Domani ho il colloquio da Verdi & Co, sono agitatissima.
[4] Dario: Respira. Se vuoi, stasera proviamo insieme le domande.
[5] Utente: Sì, grazie. Poi ti racconto com'è andata."""
EXAMPLE_ANSWER: dict[str, Any] = {
    "schema_version": 1,
    "text": "L'utente era in ansia per il colloquio del giorno dopo da Verdi & Co. Dario si è offerto di provare "
    "le domande insieme la sera stessa e lei gli ha promesso di raccontargli com'è andata.",
    "events": ["Colloquio da Verdi & Co il giorno dopo"],
    "decisions": ["Provare le domande del colloquio la sera stessa"],
    "relationship_changes": ["L'utente si affida a Dario per prepararsi"],
    "unresolved": ["Com'è andato il colloquio"],
    "key_facts": ["Ha un colloquio da Verdi & Co"],
    "source_seqs": [3, 4, 5],
}


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _one_line(text: str) -> str:
    """Returns `text` on one line, cut to MAX_MESSAGE_CHARS: a line break inside a message could otherwise start a line
    that looks like another numbered message."""
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_MESSAGE_CHARS else flat[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"


def _messages(messages: Sequence[PromptMessage]) -> str:
    return "\n".join(f"[{m.seq}] {_one_line(m.author)}: {_one_line(m.content)}" for m in messages) or _NOTHING


def _part(part: RolledCheckpoint) -> str:
    lines = [f"Riassunto dei messaggi da {part.from_seq} a {part.to_seq}: {_one_line(part.text)}"]
    for name, entries in part.lists.items():
        if entries:
            lines.append(f"{name}: " + "; ".join(_one_line(entry) for entry in entries))
    return "\n".join(lines)


def render_prompt(template: TextTemplate, data: SummaryPrompt, repair: Repair | None) -> str:
    """Returns the whole prompt in the format of `template`: the rollup form when `data.parts` is not empty, the
    message form otherwise.

    With `repair`, the prompt also carries the refused output and its error, and asks for the whole JSON again. An
    unknown language code writes the summary in Italian, as block 1 does.
    """
    language_name = LANGUAGE_NAMES.get(data.language, LANGUAGE_NAMES["it"])
    rollup = bool(data.parts)
    rules = _rules(_one_line(data.user_name), _one_line(data.character_name), language_name, rollup)
    example = f"Esempio.\n{EXAMPLE_INPUT}\nRisposta:\n{_compact(EXAMPLE_ANSWER)}"
    body = "\n\n".join(_part(part) for part in data.parts) if rollup else _messages(data.messages)
    heading = "Riassunti da unire" if rollup else "Messaggi da riassumere"
    user_parts = [f"{heading}:\n{body}"]
    if repair is not None:
        user_parts.append(
            f"La tua risposta precedente:\n{repair.previous_output}\n"
            f"Non era valida: {repair.error}\nCorreggi l'errore e riscrivi tutto il JSON."
        )
    user_parts.append("Rispondi solo con il JSON richiesto.")
    return formatter_for(template).structured(f"{rules}\n\n{example}", "\n\n".join(user_parts))
