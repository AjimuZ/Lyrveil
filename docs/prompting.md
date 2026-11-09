# Prompting and language

Status: accepted (2026-11-01), revised 2026-11-05 for selectable text models and 2026-11-06 for the prompt layout and the
voice fields. Implementation: partial. Delivered in P02.3b: `PromptFormatter` for the three families of
[Prompt format](#prompt-format) ([Backend](backend.md#prompt-formatter)). Delivered in P04.5: blocks 1, 2, 3, 5, 6 and 9
and the length bands of a reply ([System blocks](#system-blocks)). Reconciled on 2026-11-09: the prompts of the character
draft, the memory extraction, the summaries, the image scene, the visual profile translation and the
proactive message are delivered as Python modules in `backend/src/lyrveil/prompts/`, with the Italian pattern lists as
text files in `prompts/it/`. The Italian quality protocol (T-QLT-01) has not run on a real model.

**Not delivered: `PromptTemplateService` and the `.j2` files.** The design below loads versioned Jinja2 templates from
`backend/src/lyrveil/prompts/<language>/`. The code does not: Jinja2 is not a dependency, and the template listings in this
document show the wording of the prompts, which the Python modules build. The paragraph that follows describes the
design.

`PromptTemplateService` loads templates from `backend/src/lyrveil/prompts/<language>/`. Each
file has a version in its name, such as `extraction.v1.j2`. Rendering uses Jinja2 with
`StrictUndefined` and no autoescape. User and character text enters only as data values. Enum
values pass through a `words` filter that replaces `_` with a space, so `molto_informale` reads
`molto informale`.

Templates exist in Italian and English. Other languages use the English set plus a directive to
write in the target language.

## Language resolution

```text
language = conversation.language or character.primary_language or "it"
```

Supported codes in 1.0: `it`, `en`, `fr`, `de`, `es`. Memories and summaries use the conversation
language. Image prompts are always English.

## Prompt format

The prompt format depends on the active [text profile](models.md#text-profiles)
([ADR 0025](decisions/0025-prompt-templates-per-model-family.md)). Lyrveil renders the text itself and
calls `/api/extra/generate/stream`, or `/api/v1/generate` for structured tasks. `PromptFormatter`
receives the same four logical parts for every family
([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)):

| Part | Blocks | Changes when |
| --- | --- | --- |
| Stable system text | 1, 2, 3, 6 | The card is edited, the profile changes or a summary checkpoint is added |
| History | 7, as plain turns | A new exchange is appended |
| Dynamic context | 4, 5, 9, inside the frame below | Every reply |
| Current message | 8 | Every reply |

The frame of the dynamic context:

```text
[Contesto per {first_name}, non scritto da {user_display_name}]
{block 4}
{block 5}
{block 9}
[Messaggio di {user_display_name}]
{current message}
```

Old turns never carry the frame, so the stable text and the history stay byte-identical between
replies, and KoboldCpp reuses them from its cache. A line that starts with a frame marker inside user
text, character text, memories or summaries has its opening `[` replaced with `(`. Validator V2
removes markers from replies. Structured tasks keep one system text and one user turn.

KoboldCpp parses control tokens written in the prompt and adds BOS itself, so no template writes BOS.
Thinking is always off.

### `gemma4`

Used by `gemma-4-26b-a4b`, `gemma-4-12b` and `orion-26b-a4b`. Matches the official template with
thinking off:

```text
<|turn>system
{stable system text}<turn|>
<|turn>user
{older user message}<turn|>
<|turn>model
{older reply}<turn|>
<|turn>user
{frame with the dynamic context and the current message}<turn|>
<|turn>model
<|channel>thought
<channel|>
```

Stop sequences: `<turn|>`, `<|turn>`, and `\n{user_display_name}:`. Token 106, `<turn|>`, is also an
end token of the model.

### `mistral_v3_tekken`

Used by `rocinante-x-12b`. No spaces around the markers. The system text joins the last user turn,
as in the template of its GGUF, followed by the frame:

```text
[INST]{older user message}[/INST]{older reply}</s>[INST]{stable system text}

{frame with the dynamic context and the current message}[/INST]
```

The stable text moves with the last turn, so this family reprocesses it on every reply. The history
before it stays cached.

Stop sequences: `</s>`, `[INST]`, and `\n{user_display_name}:`.

### `chatml`

Used by `rocinante-12b-v1.1`. The markers are ordinary text in that tokenizer:

```text
<|im_start|>system
{stable system text}<|im_end|>
<|im_start|>user
{older user message}<|im_end|>
<|im_start|>assistant
{older reply}<|im_end|>
<|im_start|>user
{frame with the dynamic context and the current message}<|im_end|>
<|im_start|>assistant
```

Stop sequences: `<|im_end|>`, `<|im_start|>`, `</s>`, and `\n{user_display_name}:`.

### Checks

- A unit test compares each formatter with the output of the model's official template for the same
  turns (T-MOD-02). In P02.3b its golden strings are written from the formats above, because the sessions had no
  access to the official templates. The tokenization fixtures of the P02 exit check confirm them.
- A unit test renders two consecutive replies of one conversation and checks that they share the
  stable text and the history byte for byte, and that no old turn carries the frame (T-MOD-05).
- The P02 exit check tokenizes one prompt per family with the real model and stores the identifiers as
  a test fixture.

## Sampler presets

Values not listed are left to upstream defaults. Each text profile names a preset. The Gemma 4 values
start from the model card, which gives one configuration for all uses. The Mistral Nemo values start
from the requirements and the Rocinante author's advice to use DRY. P02 and P04 tune them with the
quality protocols.

| Parameter | `gemma4` chat | `gemma4` structured | `nemo` chat | `nemo` structured |
| --- | --- | --- | --- | --- |
| `temperature` | 1.0 | 0.3 | 0.8 | 0.3 |
| `top_p` | 0.95 | 0.95 | 0.95 | 0.9 |
| `top_k` | 64 | 64 | 0 | 0 |
| `min_p` | 0 | 0 | 0.05 | 0.05 |
| `rep_pen` | 1.0 | 1.0 | 1.05 | 1.0 |
| `rep_pen_range` | Unset | Unset | 512 | Unset |
| `dry_multiplier` | 0.8 | 0 | 0.8 | 0 |
| `dry_base` | 1.75 | Unset | 1.75 | Unset |
| `dry_allowed_length` | 2 | Unset | 2 | Unset |
| `dry_penalty_last_n` | 320 | Unset | 320 | Unset |
| `dry_sequence_breakers` | `["\n", ":", "\"", "*"]` | Unset | `["\n", ":", "\"", "*"]` | Unset |
| `max_length` | By length band, see below | By task, see below | By length band | By task |
| `grammar` | Unset | Generated from the schema | Unset | Generated from the schema |

Structured tasks use a low temperature because the grammar already fixes the form, and the content
should be stable. The `generic` preset has the `nemo` values and serves every custom profile, whatever
its template family ([ADR 0034](decisions/0034-character-consistency-protocol-and-verified-profiles.md)).

Chat values can be changed in **Impostazioni > Modelli**. A changed value belongs to the profile that
was active, so switching profiles restores that profile's values.

### Length bands

The character's `message_length` sets `max_length` and the length text of the anchor
([ADR 0032](decisions/0032-model-independent-reply-normalization.md)). `llm.max_length`, default 400,
caps every band. Targets are median lengths in tokens, measured by the
[consistency protocol](consistency-engine.md#consistency-protocol).

| Band | `max_length` | Target median | Length text |
| --- | --- | --- | --- |
| `brevissimi` | 60 | At most 30 | Rispondi con una frase sola, brevissima. |
| `brevi` | 120 | At most 70 | Rispondi in una o due frasi brevi. |
| `medi` | 240 | At most 160 | Rispondi in poche frasi. |
| `lunghi` | 400 | At most 300 | Puoi scrivere un messaggio lungo, se serve. |

Delivered in P04.5 (`lyrveil/context/blocks.py`): `reply_length` gives the band's `max_length` capped by `llm.max_length`;
every reply sends it and reserves it in the window. The target medians are measured in P04.10.

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
{% if user.grammatical_gender != "non_indicato" %}- Quando ti rivolgi a {{ user.display_name }}, usa il {{ user.grammatical_gender }}.
{% endif %}
Come funziona questa chat:
- Prima di ogni messaggio di {{ user.display_name }} trovi un contesto tra parentesi quadre. Non l'ha scritto {{ user.display_name }}: usalo per rispondere, ma non citarlo e non commentarlo.

Cosa sai:
- Conosci solo la tua scheda, il vostro stato, i ricordi nel contesto e i messaggi di questa chat.
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
{% if character.grammatical_gender != "non_indicato" %}Parli di te al {{ character.grammatical_gender }}.{% endif %}
{% if character.occupation %}Lavoro: {{ character.occupation }}.{% endif %}
{% if character.setting %}Dove vivi: {{ character.setting }}.{% endif %}
{% if character.biography %}Storia: {{ character.biography }}{% endif %}
{% if character.background %}Dal tuo passato: {{ character.background }}{% endif %}
{% if character.interests %}Interessi: {{ character.interests | join(", ") }}.{% endif %}

Carattere: {{ traits.main_trait | join("; ") }}
Valori: {{ traits.value | join("; ") }}
Motivazioni: {{ traits.motivation | join("; ") }}
Preferenze: {{ traits.preference | join("; ") }}
Difetti: {{ traits.flaw | join("; ") }}
Paure: {{ traits.fear | join("; ") }}
Abitudini: {{ traits.habit | join("; ") }}
Umorismo: {{ traits.humor | join("; ") }}
Temperamento: {{ traits.temperament | join("; ") }}
Con gli altri: {{ traits.relational_style | join("; ") }}
```

Empty lines are omitted. The background, interests and preference lines were added in P04.5: the reduction order of
[context composition](memory-engine.md#algorithm) names interests and background details as identity fields of the
prompt, and preferences are a trait category of the card. A line that adds a period does not add a second one to a
value that already ends a sentence.

### Block 3: communication profile

```jinja
Come scrivi in chat:
Registro: {{ comm.formality | words }}. Lunghezza: {{ comm.message_length | words }}. Domande: {{ comm.question_frequency | words }}.
Punteggiatura: {{ comm.punctuation }}. Stile: {{ comm.colloquial_style }}. Espressività: {{ comm.expressiveness | words }}.
Dai del {{ comm.address_form }} a {{ user.display_name }}. {{ narration_text }} {{ emoji_text }}
{% if comm.recurring_expressions %}Espressioni tue: {{ comm.recurring_expressions | join(", ") }}. Non usarle in ogni messaggio.{% endif %}
{% if comm.avoid %}Non diresti mai: {{ comm.avoid | join("; ") }}.{% endif %}
{% if comm.examples %}Esempi di stile, non cose successe:
{% for example in comm.examples %}{% if example.user %}{{ user.display_name }}: {{ example.user }}
{% endif %}{{ character.first_name }}: {{ example.character }}
{% endfor %}{% endif %}
```

Examples are rendered in saved order. Rendering stops before the first example that would take block 3 over its maximum
of 500 tokens, and under budget pressure examples are the first thing cut, from the last one
([context composition](memory-engine.md#context-composition)). The editor shows how many examples reach the prompt, so
the first examples should be the most typical ones. In P04.5 an empty `punctuation` or `colloquial_style` leaves its
sentence out of the second line; the editor's count of examples is not delivered.

Fixed texts for `narration` and `emoji` ([ADR 0033](decisions/0033-card-fields-for-a-model-independent-voice.md)):

| Value | Text |
| --- | --- |
| `narration` `nessuna` | Scrivi solo le parole del messaggio, senza azioni o descrizioni tra asterischi. |
| `narration` `leggera` | Al massimo un breve gesto tra asterischi, e non in ogni messaggio. |
| `narration` `libera` | Puoi descrivere gesti e azioni tra asterischi. |
| `emoji` `mai` | Non usare emoji. |
| `emoji` `raramente` | Usa le emoji solo di rado. |
| `emoji` `a_volte` | Ogni tanto puoi usare un'emoji. |
| `emoji` `spesso` | Usi spesso le emoji. |

### Block 4: state and time

Part of the dynamic context.

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

Delivered in P04.6 as `state_lines` in `lyrveil/context/blocks.py`, for a chat with a `relationship_states` row; a chat
without one has no block 4. `bands_text` joins the four band phrases with `, `, in the order familiarity, trust,
confidence, tension. The bands of [From numbers to text](consistency-engine.md#from-numbers-to-text) are worded from the
user's side, while this block speaks to the character, so block 4 turns them to the second person with
`user.display_name`:

| Dimension | 0 to 19 | 20 to 39 | 40 to 69 | 70 to 100 |
| --- | --- | --- | --- | --- |
| Familiarity | vi siete appena conosciuti | vi state conoscendo | vi conoscete bene | vi conoscete a fondo |
| Trust | diffidi di {user} | sei prudente con {user} | ti fidi di {user} | ti fidi profondamente di {user} |
| Confidence | parli poco di te | ti apri su cose leggere | racconti a {user} cose personali | con {user} parli di tutto |
| Tension | nessuna tensione | qualche attrito | tensione evidente | rapporto molto teso |

A line whose value already ends a sentence gets no second period. The relationship, emotional state, topic and
boundaries lines are always kept; the open threads, each without its final period and joined with `; `, are the
details that the budget removes last. The scenario line is left out, because the scenario copy is already in the stable
text. The topic and boundaries are always empty for now.

Delivered in P07.4: the two time lines come from `lyrveil/context/time_context.py` (`time_lines`) and close block 4,
after the details, never reduced. They are in the block even for a chat with no `relationship_states` row. The date
reads `Adesso è domenica di sera, 1 novembre.`; the last interaction is the newest readable message before the reply
(or, for a proactive message, the newest of the chat), worded as `poco fa`, `qualche ora fa`, `oggi`, `ieri`, `tre
giorni fa` (to thirteen days), `due settimane fa`, `un mese fa`, `tre mesi fa` or `più di un anno fa`, and left out for a
chat with no earlier message. Part of the dynamic context, so the stable text and the history are unchanged (T-MOD-05).
Image captions do not carry the time lines.

### Blocks 5 and 6

Rendering is defined in [Memory engine](memory-engine.md#prompt-rendering). Block 5 is part of the
dynamic context. Block 6 is part of the stable text, and it starts with
`Riassunto di quello che è successo prima:`.

### Block 9: anchor

`anchor.v1.j2`, the last lines before the current message
([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)). It takes 60 to 160 tokens and
is never reduced:

```jinja
Ricorda: sei {{ character.full_name }} e hai {{ character.age }} anni.{% if character.grammatical_gender != "non_indicato" %} Parli di te al {{ character.grammatical_gender }}.{% endif %}
{% if anchor_traits %}{{ anchor_traits | join(" ") }}{% endif %}
Dai del {{ comm.address_form }} a {{ user.display_name }}. {{ length_text }} {{ narration_text }} {{ emoji_text }}
{% for note in notes %}{{ note }}
{% endfor %}
```

`anchor_traits` holds the first sentence of up to two main traits, in their saved order; a sentence ends at the first
`.`, `!`, `?` or `…` followed by a space, and one without an ending gets a period. `notes` holds
up to two corrective notes, chosen by the [consistency engine](consistency-engine.md#reply-checks). Since P04.8b the
texts are in `prompts/it/notes.txt`. The note of a corrective generation comes first, then the adaptive notes, oldest
first ([Delivered in P04.8b](consistency-engine.md#delivered-in-p048b)):

| Trigger | Note | Lasts |
| --- | --- | --- |
| V6 | Ti chiami {{ character.first_name }}. | The regenerated reply |
| V10 | Hai {{ character.age }} anni. | The regenerated reply |
| V11 | Parli di te al {{ character.grammatical_gender }}. | The regenerated reply |
| V7 twice in a row | Non cominciare come nei tuoi ultimi messaggi. | 3 replies |
| V8 | Scrivi solo in {{ language_name }}. | 3 replies |
| V9 | Evita le frasi fatte e i calchi dall'inglese. | 3 replies |
| V13 | Dai del {{ comm.address_form }} a {{ user.display_name }}, sempre. | 3 replies |

## Structured tasks

All structured tasks use the same flow ([ADR 0013](decisions/0013-structured-output-grammar-and-validation.md)):

1. Build the GBNF grammar from the Pydantic model with `schema_to_gbnf`. It supports objects with fixed
   keys, enums, integers with ranges, strings, nullable values and arrays.
2. Generate with `/api/v1/generate`.
3. Parse and validate with Pydantic, then check semantic rules.
4. On failure, try once more with the errors appended.
5. On a second failure, fail with `STRUCTURED_OUTPUT_INVALID`.

Model output is untrusted: it is never executed, never placed in SQL or a path, and rendered as text.

Delivered in P03.3, with the character draft as the first task:

- `schema_to_gbnf` (`lyrveil/prompts/gbnf.py`) reads the model's JSON Schema, with `$defs` as rules. It writes every key
  of an object in schema order, lists an integer range of up to 256 values (the age is `"18" | ... | "120"`), allows at
  most one whitespace character between tokens, and bounds an array with nested optionals instead of `{m,n}`. Lengths
  and patterns stay with validation. Any other construct, such as a free-key object or a float, raises
  `UnsupportedSchemaError`.
- `generate_structured` (`lyrveil/prompts/structured.py`) runs steps 2 to 5 under the caller's lease: the repair prompt
  carries the refused output and the Italian reason, which names fields and never values. It raises
  `StructuredOutputInvalidError`, which the job handler turns into `STRUCTURED_OUTPUT_INVALID`, not retried.
- Templates are Python modules for now (`lyrveil/prompts/character_draft.py`), because the backend has no template
  engine yet; the `.j2` names below are the planned files. The draft uses temperature 0.4.

Delivered in P04.2a, with memory extraction as the second task:

- `schema_to_gbnf` ignores `format` as it ignores lengths and patterns: a `date` is a string to the grammar, and
  validation parses it.
- `generate_structured` also returns the accepted text, and `StructuredOutputInvalidError.output` holds the last
  refused one, so extraction can keep `raw_output`. Both are private and never logged.
- The extraction template is `lyrveil/prompts/extraction.py`, version `extraction.v1` (`extraction.v2` since P04.2b, `extraction.v3` since P04.6), at temperature 0.3 (the structured
  value of [Sampler presets](#sampler-presets)) and `max_length` 1200. See [Extraction template](#extraction-template).

Each template holds one worked example of input and output. The example stays only if it does not
lower the score of the [extraction evaluation](consistency-engine.md#extraction-evaluation)
(T-QLT-06).

| Task | Template | Model | `max_length` |
| --- | --- | --- | --- |
| Memory extraction | `extraction.v3.j2` | `ExtractionResult` | 1200 |
| Summary checkpoint | `summary.v1.j2` (Python, `lyrveil/prompts/summary.py`, since P04.4) | `SummaryResult` | 700 |
| Character draft | `character_draft.v1.j2` | `CharacterDraft` | 1500 |
| Visual identity | `visual_profile.v1.j2` (Python, `lyrveil/prompts/visual_profile.py`, since P05.3b) | `VisualIdentityResult` | 600 |
| Image scene | `image_scene.v1.j2` (Python, `lyrveil/prompts/image_scene.py`, since P05.4a) | `ImageScene` | 400 |
| Image proposal | `image_proposal.v1.j2` | `ImageProposalResult` | 200 |
| Proactive decision | `proactive_decision.v1.j2` | `ProactiveDecision` | 150 |

The image caption is not a structured task. It uses the chat path with the anchor
([Image generation](image-generation.md#caption)).

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
- [incerto] segna ricordi in conflitto: usa "update" o "correction" solo se i messaggi chiariscono quale vale.
- Se qualcosa sembra cambiare la scheda del personaggio, usa il tipo "identity_candidate": è solo una proposta.
- "subject" è una chiave breve come user.employer, user.city, user.likes. Omettila se non è adatta.
- Cita sempre i numeri dei messaggi che lo provano in "source_seqs".
- Scrivi i contenuti in {{ language_name }}, in terza persona, in una frase breve.
- Registra un episodio solo per eventi davvero importanti.
- Per il rapporto registra solo cambiamenti con un motivo chiaro.
- Per chiudere un discorso in sospeso, ripeti il suo testo in "open_threads_close".

Ricordi esistenti:
{% for memory in existing %}{{ memory.ref }}: {% if memory.status == "uncertain" %}[incerto] {% endif %}{{ memory.content }}
{% else %}nessuno
{% endfor %}
Episodi recenti:
{% for episode in episodes %}{{ episode.ref }}: {{ episode.title }}
{% else %}nessuno
{% endfor %}
Discorsi in sospeso:
{% for thread in state.open_threads %}- {{ thread }}
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

Delivered in P04.2a as `lyrveil/prompts/extraction.py`, with the text above. The two opening sentences, the rules and
one worked example (an `update` of a job, which a unit test validates against `ExtractionResult`) form the system turn;
the data and "Rispondi solo con il JSON richiesto." form the user turn, and a repair adds the refused output and its
error before that last line. `author_label` is `user.display_name` or the character's first name. Each message is written on
one line and cut at 1200 characters, and an empty context section reads `nessuno` like the other lists.

P04.2b made it `extraction.v2`: an `uncertain` memory, part of an unresolved conflict, is shown after `[incerto]`, and the
rule line about that label is new. `identity_candidate` memories are never shown.

P04.6 made it `extraction.v3`: the open threads of the conversation state are listed under `Discorsi in sospeso:`, one
per line after `- ` and cut like a message, with the rule line that asks to repeat a thread's text to close it. The
worked example shows the section as `nessuno`. A close is matched against the open threads as memories are
deduplicated ([Delivered in P04.6](consistency-engine.md#delivered-in-p046)).

### Other templates

`summary`, `character_draft`, `visual_profile`, `image_scene`, `image_proposal` and `proactive_decision`
follow the same shape: a role sentence, numbered rules, data, the worked example and "Rispondi solo con il
JSON richiesto". Their schemas and rules are in [Memory engine](memory-engine.md#progressive-summaries),
[Character system](character-system.md#assisted-creation), [Image generation](image-generation.md#visual-promptbuilder)
and [Scheduler](scheduler.md#generation-flow).

## Italian quality

No profile states Italian by name. Gemma 4 states more than 35 supported languages, Mistral Nemo lists
Italian for the base model, and the roleplay finetunes state nothing about it. Quality is therefore a
measured risk ([Risks](risks.md)). The levers are:

- A character's example exchanges, which have the greatest influence on tone.
- The anchor, the length bands and the [reply normalizer](consistency-engine.md#reply-normalization),
  which give every profile the same surface form.
- Global rules against calques and formula openings.
- DRY and repetition penalty.
- The choice of profile, compared with the protocols below.
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

Delivered in P04.8b. `^` marks an opening and `+` a phrase that counts only once one of the last 10 replies holds it
too. `Fa senso` is listed in its calque uses (`questo fa senso`, `non fa senso`, `fa senso che`), because `mi fa senso`
is correct Italian. The character-break row is checked by V5 from `prompts/it/model_talk.txt`, which leaves out `come
modello` and `in quanto ai`: `come modello di vita` and `in quanto ai prezzi` are plain Italian.

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

P04 runs the protocol on the default text profile, `orion-26b-a4b` and `rocinante-x-12b`, with the same scripts.
The [consistency protocol](consistency-engine.md#consistency-protocol) (T-QLT-05) and the
[extraction evaluation](consistency-engine.md#extraction-evaluation) (T-QLT-06) run on every catalog profile.

On failure the order is fixed: improve the examples and templates, then tune the samplers, and repeat. If the default
still misses a target, the [default rule](models.md#defaults-and-how-they-can-change) applies. The owner authorized
that rule on 2026-11-05. Lyrveil never changes the user's active profile by itself.
