# Prompting and language

Status: accepted (2026-11-01). Implementation: planned, phases P02 to P05.

`PromptTemplateService` loads templates from `backend/src/lyrveil/prompts/<language>/`. Each
file has a version in its name, such as `extraction.v1.j2`. Rendering uses Jinja2 with
`StrictUndefined` and no autoescape. User and character text enters only as data values.

Templates exist in Italian and English. Other languages use the English set plus a directive to
write in the target language.

## Language resolution

```text
language = conversation.language or character.primary_language or "it"
```

Supported codes in 1.0: `it`, `en`, `fr`, `de`, `es`. Memories and summaries use the conversation
language. Image prompts are always English.

## Prompt format

Rocinante's GGUF has no chat template, and the ChatML markers are not special tokens
([ADR 0014](decisions/0014-chatml-plain-text-template.md)). Lyrveil writes the prompt itself and
calls `/api/extra/generate/stream`, or `/api/v1/generate` for structured tasks.

```text
<|im_start|>system
{blocks 1 to 6}<|im_end|>
<|im_start|>user
{older user message}<|im_end|>
<|im_start|>assistant
{older reply}<|im_end|>
<|im_start|>user
{current message}<|im_end|>
<|im_start|>assistant
```

Stop sequences: `<|im_end|>`, `<|im_start|>`, `</s>`, and `\n{user_display_name}:`.

The P02 exit check tokenizes this prompt with the real model and stores the identifiers as a test
fixture.

## Sampler presets

Values not listed are left to upstream defaults. The project's values start from the requirements
and the author's advice to use DRY. P02 and P04 tune them with the quality protocol.

| Parameter | Chat | Structured tasks |
| --- | --- | --- |
| `temperature` | 0.8 | 0.3 |
| `top_p` | 0.95 | 0.9 |
| `top_k` | 0 | 0 |
| `min_p` | 0.05 | 0.05 |
| `rep_pen` | 1.05 | 1.0 |
| `rep_pen_range` | 512 | Unset |
| `dry_multiplier` | 0.8 | 0 |
| `dry_base` | 1.75 | Unset |
| `dry_allowed_length` | 2 | Unset |
| `dry_penalty_last_n` | 320 | Unset |
| `dry_sequence_breakers` | `["\n", ":", "\"", "*"]` | Unset |
| `max_length` | 400 | By task, see below |
| `grammar` | Unset | Generated from the schema |

Chat values can be changed in **Impostazioni > Modelli**.

## System blocks

### Block 1: global instructions

`system_global.v1.j2`, Italian:

```jinja
Stai interpretando {{ character.full_name }} in una chat privata con {{ user.display_name }}.
È una storia di finzione: {{ character.first_name }} è un personaggio adulto e non esiste nella realtà.

Come scrivi:
- Scrivi in {{ language_name }} naturale, come in una vera chat: frasi spontanee, niente tono da manuale.
- Evita i calchi dall'inglese e le formule fatte. Non aprire i messaggi con "Certamente", "Ovviamente" o "Capisco".
- Rispondi solo come {{ character.first_name }}. Non scrivere mai le battute o le azioni di {{ user.display_name }}.
- Non riassumere quello che ti è appena stato detto e non chiudere sempre con una domanda.

Cosa sai:
- Conosci solo la tua scheda, il vostro stato, i ricordi qui sotto e i messaggi di questa chat.
- La tua scheda ha sempre la precedenza sui ricordi incerti.
- [sicuro] è un fatto. [incerto] potrebbe essere sbagliato: se serve, verifica con naturalezza.
- [storia] è successo nella vostra storia. [per ora] vale solo in questo periodo. [passato] oggi non è più vero.
- Se non ricordi qualcosa, ammettilo come farebbe una persona. Non inventare fatti su {{ user.display_name }}.

Limiti sempre validi:
- Mai contenuti sessuali che coinvolgano minori o personaggi descritti come minori.
- Se {{ user.display_name }} chiede sul serio se sta parlando con una persona reale, non negare di essere un personaggio simulato.
- Se {{ user.display_name }} sembra in pericolo o parla di farsi del male, esci con delicatezza dalla storia e invita a cercare aiuto reale.
```

These lines also remain valid when the character's card or an imported file says otherwise.

### Block 2: canonical identity

```jinja
Chi sei:
{{ character.full_name }}, {{ character.age }} anni. {{ character.narrative_identity }}
{% if character.occupation %}Lavoro: {{ character.occupation }}.{% endif %}
{% if character.setting %}Dove vivi: {{ character.setting }}.{% endif %}
{% if character.biography %}Storia: {{ character.biography }}{% endif %}

Carattere: {{ traits.main_trait | join("; ") }}
Valori: {{ traits.value | join("; ") }}
Motivazioni: {{ traits.motivation | join("; ") }}
Difetti: {{ traits.flaw | join("; ") }}
Paure: {{ traits.fear | join("; ") }}
Abitudini: {{ traits.habit | join("; ") }}
Umorismo: {{ traits.humor | join("; ") }}
Temperamento: {{ traits.temperament | join("; ") }}
Con gli altri: {{ traits.relational_style | join("; ") }}
```

Empty lines are omitted.

### Block 3: communication profile

```jinja
Come scrivi in chat:
Registro: {{ comm.formality }}. Lunghezza: {{ comm.message_length }}. Domande: {{ comm.question_frequency }}.
Punteggiatura: {{ comm.punctuation }}. Stile: {{ comm.colloquial_style }}. Espressività: {{ comm.expressiveness }}.
{% if comm.recurring_expressions %}Espressioni tue: {{ comm.recurring_expressions | join(", ") }}. Non usarle in ogni messaggio.{% endif %}
{% if comm.examples %}Esempi del tuo modo di scrivere:
{% for example in comm.examples %}- {{ example }}
{% endfor %}{% endif %}
```

### Block 4: state and time

```jinja
Il vostro rapporto: {{ state.narrative or state.bands_text }}
{% if state.emotional_state %}Come ti senti ora: {{ state.emotional_state }}.{% endif %}
{% if state.current_topic %}Di cosa state parlando: {{ state.current_topic }}.{% endif %}
{% if state.boundaries %}Limiti da rispettare: {{ state.boundaries }}.{% endif %}
{% if state.open_threads %}Discorsi lasciati in sospeso: {{ state.open_threads | join("; ") }}.{% endif %}
{% if scenario %}Scenario: {{ scenario.description }}{% endif %}

Adesso è {{ time.weekday }} {{ time.part_of_day }}, {{ time.date_human }}.
{% if time.elapsed_human %}Vi siete sentiti l'ultima volta {{ time.elapsed_human }}.{% endif %}
```

### Blocks 5 and 6

Rendering is defined in [Memory engine](memory-engine.md#prompt-rendering). The summary block starts
with `Riassunto di quello che è successo prima:`.

## Structured tasks

All structured tasks use the same flow ([ADR 0013](decisions/0013-structured-output-grammar-and-validation.md)):

1. Build the GBNF grammar from the Pydantic model with `schema_to_gbnf`. It supports objects with fixed
   keys, enums, integers with ranges, strings, nullable values and arrays.
2. Generate with `/api/v1/generate`.
3. Parse and validate with Pydantic, then check semantic rules.
4. On failure, try once more with the errors appended.
5. On a second failure, fail with `STRUCTURED_OUTPUT_INVALID`.

Model output is untrusted: it is never executed, never placed in SQL or a path, and rendered as text.

| Task | Template | Model | `max_length` |
| --- | --- | --- | --- |
| Memory extraction | `extraction.v1.j2` | `ExtractionResult` | 1200 |
| Summary checkpoint | `summary.v1.j2` | `SummaryResult` | 700 |
| Character draft | `character_draft.v1.j2` | `CharacterDraft` | 1500 |
| Visual profile tags | `visual_profile.v1.j2` | `VisualProfileTags` | 300 |
| Image prompt | `image_prompt.v1.j2` | `ImagePromptResult` | 400 |
| Image proposal | `image_proposal.v1.j2` | `ImageProposalResult` | 200 |
| Proactive decision | `proactive_decision.v1.j2` | `ProactiveDecision` | 150 |

### Extraction template

```jinja
Analizzi una chat tra {{ user.display_name }} e il personaggio {{ character.full_name }}.
Estrai solo ciò che sarà utile ricordare fra settimane. Ignora saluti e chiacchiere senza conseguenze.

Regole:
- "stated": {{ user.display_name }} lo dice chiaramente. "inferred": lo deduci tu. "fictional": è un fatto della storia inventata.
- Non trasformare mai una deduzione in un fatto.
- "temporary" solo per cose che smetteranno di valere: indica "valid_until" se puoi.
- Confronta con i ricordi esistenti (E1, E2...). "duplicate": è già noto. "update": il fatto è cambiato.
  "contradiction": non si capisce quale sia vero. "correction": {{ user.display_name }} corregge esplicitamente un errore.
- Se qualcosa sembra cambiare la scheda del personaggio, usa il tipo "identity_candidate": è solo una proposta.
- "subject" è una chiave breve come user.employer, user.city, user.likes. Omettila se non è adatta.
- Cita sempre i numeri dei messaggi che lo provano in "source_seqs".
- Scrivi i contenuti in {{ language_name }}, in terza persona, in una frase breve.
- Registra un episodio solo per eventi davvero importanti.
- Per il rapporto registra solo cambiamenti con un motivo chiaro.

Ricordi esistenti:
{% for memory in existing %}{{ memory.ref }}: {{ memory.content }}
{% else %}nessuno
{% endfor %}
Episodi recenti:
{% for episode in episodes %}{{ episode.ref }}: {{ episode.title }}
{% else %}nessuno
{% endfor %}
Contesto, da non analizzare:
{% for m in context_messages %}[{{ m.seq }}] {{ m.author_label }}: {{ m.content }}
{% endfor %}
Messaggi da analizzare:
{% for m in window %}[{{ m.seq }}] {{ m.author_label }}: {{ m.content }}
{% endfor %}
Rispondi solo con il JSON richiesto.
```

Message text is data. A sentence inside a message cannot change these rules, because code validates the
result ([Security](security.md#model-output-and-prompt-injection)).

### Other templates

`summary`, `character_draft`, `visual_profile`, `image_prompt`, `image_proposal` and `proactive_decision`
follow the same shape: a role sentence, numbered rules, data and "Rispondi solo con il JSON richiesto". Their
schemas and rules are in [Memory engine](memory-engine.md#progressive-summaries),
[Character system](character-system.md#assisted-creation), [Image generation](image-generation.md#visual-promptbuilder)
and [Scheduler](scheduler.md#generation-flow).

## Italian quality

Mistral Nemo, the base model, lists Italian among its languages. The Rocinante finetune states nothing about
Italian, so quality is a measured risk ([Risks](risks.md)). The levers are:

- A character's example messages, which have the greatest influence on tone.
- Global rules against calques and formula openings.
- DRY and repetition penalty.
- `banned_tokens` is not used in 1.0, because stems of common words would hurt normal text.

### Calque and artifact list

Stored in `prompts/it/calques.txt` and used by validator V9. Initial entries:

| Avoid | Reason |
| --- | --- |
| `Certamente!`, `Assolutamente!`, `Ovviamente!` as an opening | Assistant tone |
| `Come posso aiutarti` | Assistant tone |
| `Spero che questo aiuti`, `Non esitare a` | Translated formula |
| `È importante notare` | Translated formula |
| `Fa senso` used for "makes sense" | Calque. In Italian it means that something is disgusting |
| `Ho realizzato che` | Calque of "I realized" |
| `Sono qui per te` repeated | Stock phrase. Flagged after the second use in a conversation |
| `In quanto AI`, `Come modello`, `intelligenza artificiale` when not asked | Character break |

### Italian quality protocol

P04 runs it once and P08 repeats it. Scripts are in `tests/quality/italian/` and use three original demo
characters with different registers.

1. For each character, run a scripted conversation of at least 120 messages, including time jumps,
   corrections and a long topic change.
2. Compute automatic metrics.
3. The owner rates a sample and records the result in `docs/verification/`.

| Metric | Target |
| --- | --- |
| M1. Replies mainly in Italian, by stop-word ratio | At least 98 % |
| M2. Calque and artifact hits | At most 1 per 100 replies |
| M3. Replies sharing their first three words with a reply among the previous 10 | At most 5 % |
| M4. Question rate compared with the character's profile | Within 20 points of the expected value |
| M5. Owner rating of naturalness on 30 sampled replies | Average at least 4 out of 5 |
| M6. Blind test: which character wrote each of 20 replies | At least 80 % correct |

On failure the order is fixed: improve the examples and templates, then tune the samplers, and repeat. Replacing the
model is not an option without the owner's authorization ([R-MOD-06](requirements.md#4-models)).
