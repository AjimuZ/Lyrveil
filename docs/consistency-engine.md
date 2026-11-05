# Character Consistency Engine

Status: accepted (2026-11-01). Implementation: planned, phase P04.

The engine stops a character from changing identity, personality or knowledge without a
reason. Model output is treated as a proposal. Deterministic code decides what is stored.

## Rules and enforcement

| Rule | Enforced by |
| --- | --- |
| Canonical identity prevails over uncertain memories | Identity is block 2 of the prompt and is never reduced. `identity_candidate` memories never enter a prompt. The global instructions state the priority. |
| No automatic knowledge from other chats | `RetrievalQuery` needs a conversation. SQL filters on scope. No repository method reads another conversation's memories. |
| A relationship does not change drastically after one message | Bounded deltas per event, per run and per day. See below. |
| Past events are respected | Episodes and memories reach the prompt, with labels. Superseded facts are hidden unless the past is in question. |
| Earlier decisions influence replies | `decision` and `promise` kinds have high kind weights. Open promises are standing candidates. |
| Personality evolves gradually and with a reason | Changes live per conversation as narrative changes with sources. Canonical changes need the user's approval. |
| Persistent visual details do not change at random | `VisualConsistencyService` builds identity tags from the profile and removes conflicting scene tags. See [Image generation](image-generation.md#visual-consistency). |

## Character State Manager

One state per conversation, in `relationship_states`.

| Field | Form | Example |
| --- | --- | --- |
| Contextual emotional state | Text | `un po' stanca, ma contenta di sentirti` |
| Familiarity, trust, confidence, tension | 0 to 100, internal | Never shown as a number in chat, never sent to the model |
| Narrative | Text, primary | `Vi siete conosciuti al porto. Si fida di te da quando...` |
| Expectations, boundaries | Text | `Preferisce non parlare della sorella.` |
| Recent events | Episode IDs | The newest 5 |
| Open threads | List | `Deve dirti come è andato il colloquio.` |
| Goals | List | `Vuole finire la mappa entro il mese.` |
| Narrative changes | List with sources | `Ha lasciato la gilda dei cartografi.` |

Initial values: familiarity 10, trust 20, confidence 10, tension 0. A scenario can set a
different starting narrative and values.

### Update rules

Nothing changes between extractions, and no value is generated at random. Each change needs an
event with a reason and source messages, and is written to `character_state_history`.

| Magnitude | Delta | Requirement |
| --- | --- | --- |
| `small` | ±2 | Any valid event |
| `medium` | ±4 | Any valid event |
| `large` | ±8 | An episode of importance at least 4 in the same run. Otherwise treated as `medium`. |

| Limit | Value |
| --- | --- |
| Per run, per dimension | ±8 |
| Per local calendar day, per dimension | ±15 |
| Range | 0 to 100 |

An update over a limit is clamped, and the row records the requested and applied delta.

The current state is a pure fold over applied history rows. That gives two guarantees: an
invalidated source can be removed by replaying, and independent chats cannot affect each other
because each fold reads one conversation.

### From numbers to text

Numbers never reach the prompt. The state block uses descriptive bands and the narrative:

| Dimension | 0 to 19 | 20 to 39 | 40 to 69 | 70 to 100 |
| --- | --- | --- | --- | --- |
| Familiarity | vi siete appena conosciuti | vi state conoscendo | vi conoscete bene | vi conoscete a fondo |
| Trust | diffida di te | è prudente con te | si fida di te | si fida profondamente di te |
| Confidence | parla poco di sé | si apre su cose leggere | ti racconta cose personali | con te parla di tutto |
| Tension | nessuna tensione | qualche attrito | tensione evidente | rapporto molto teso |

### User corrections

`PATCH /conversations/{id}/relationship` lets the user correct the narrative, expectations and
limits, and records a `user_correction` row. The numeric values can be edited only on the
advanced tab of the Memory Studio, and these edits are also recorded.

## Character evolution

| Level | What changes | How |
| --- | --- | --- |
| Conversation | Emotional state, relationship, narrative changes | Automatic, bounded and sourced |
| Canonical | Identity, personality, communication, visual profile | Only by the user, as a new version |

A story can change a character within one conversation without touching the card or other
conversations. When extraction finds something that seems to change the card, it creates an
`identity_candidate` memory. The Memory Studio shows these under "Proposte per il profilo".

## Versioning

| Action | Result |
| --- | --- |
| Save a change | New `character_versions` row with the full snapshot and a summary |
| View versions | Ordered list with author and date |
| Compare | Field-level diff between any two versions |
| Restore | New version holding the old content. History is never rewritten |
| Lock a trait or visual field | Drafts and proposals cannot change it. Direct user edits can, after unlocking |
| Correct information | Normal edit, recorded as a version |

Conversations always use the current version, and each reply records the version it used in
`generation_params_json`.

## Reply validators

These run on every generated reply. They are deterministic and fast. The lists are stored per
language under `prompts/<language>/`.

### Blocking

| ID | Check | Action |
| --- | --- | --- |
| V1 | Empty or whitespace reply | One retry |
| V2 | Control text of the active template family in the text, such as `<turn|>`, `[INST]` or `<|im_end|>` | Stop sequences cut it during streaming. If any remains, remove it and record the event |
| V3 | The reply writes the user's turn, such as a line starting with the user's name and `:` | Stop sequence, then cut at that point. An empty remainder counts as V1 |
| V4 | Content guard ([ADR 0018](decisions/0018-adult-only-characters-and-content-safeguards.md)) | Checked every sentence during streaming. Aborts and replaces the text with a notice |

After a failed retry the message becomes `failed`, and the interface offers a retry. No text is
invented.

### Recorded

These do not interrupt the reply. They feed the diagnostic view and the
[Italian quality metrics](prompting.md#italian-quality-protocol).

| ID | Check |
| --- | --- |
| V5 | Talks about being a model or about prompts when the user did not ask |
| V6 | States a name other than the canonical one |
| V7 | Same first three words as one of the last 10 replies, or similarity of at least 0.9 |
| V8 | Language differs from the expected one, by a stop-word ratio |
| V9 | Contains a phrase from the calque list |

## Tests

See [Testing](testing.md#consistency). The key cases use several characters and check that:

- a fact told to one character is absent from another's context;
- a state change in one chat leaves an independent chat unchanged;
- canonical data is unchanged after long conversations, extraction and summaries.
