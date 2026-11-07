# Character Consistency Engine

Status: accepted (2026-11-01), revised 2026-11-06 for consistency across text profiles. Implementation: planned, phase
P04. Delivered in P04.2b: extraction never writes canonical data, and an item the model labels `identity_candidate` is
stored as a memory that no prompt shows and that changes nothing else
([Delivered in P04.2b](memory-engine.md#delivered-in-p042b)). Code does not yet detect a card change the model did not
label; "Applica al profilo" is P04.9. Delivered in P04.6: the Character State Manager with bounded updates and replay
([Delivered in P04.6](#delivered-in-p046)). Delivered in P04.8a and P04.8b: the reply normalizer
([Delivered in P04.8a](#delivered-in-p048a)), the reply checks V1 to V13, the corrective generation and the adaptive
notes ([Delivered in P04.8b](#delivered-in-p048b)).

The engine stops a character from changing identity, personality or knowledge without a
reason. Model output is treated as a proposal. Deterministic code decides what is stored and
what reaches the user, so the character stays the same whichever verified profile is active.

## Rules and enforcement

| Rule | Enforced by |
| --- | --- |
| Canonical identity prevails over uncertain memories | Identity is block 2 of the prompt and is never reduced. Block 9, the anchor, repeats the name, age, gender and main traits right before each reply ([ADR 0031](decisions/0031-stable-prefix-dynamic-context-and-anchor.md)). `identity_candidate` memories never enter a prompt. The global instructions state the priority. |
| The same identity, voice, register and gender on every verified profile | The anchor, the [length bands](prompting.md#length-bands), the [reply normalizer](#reply-normalization) and the [corrected checks](#corrected) work the same way for every profile. The [consistency protocol](#consistency-protocol) verifies each catalog profile ([ADR 0034](decisions/0034-character-consistency-protocol-and-verified-profiles.md)). |
| No automatic knowledge from other chats | `RetrievalQuery` needs a conversation. SQL filters on scope. No repository method reads another conversation's memories. |
| A relationship does not change drastically after one message | Bounded deltas per event, per run and per day. See below. |
| Past events are respected | Episodes and memories reach the prompt, with labels. Superseded facts are hidden unless the past is in question. |
| Earlier decisions influence replies | `decision` and `promise` kinds have high kind weights. Open promises are standing candidates. |
| Personality evolves gradually and with a reason | Changes live per conversation as narrative changes with sources. Canonical changes need the user's approval. |
| Persistent visual details do not change at random | `VisualConsistencyService` renders identity from structured slots, identical for every text profile, and removes scene tags of identity categories. See [Image generation](image-generation.md#visual-consistency). |

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

### Delivered in P04.6

`CharacterStateManager` is `services/character_state.py`, over the pure rules of `lyrveil/consistency/state.py` and the
SQL of `persistence/relationship.py`. `MemoryExtractionService` calls it in the commit transaction of each run, after the
memories and episodes, so the state rows are written with the run or not at all.

- **Fold.** `replay(rows, zone)` is the only way a state is computed: the applied rows of one conversation, in the order
  written (`created_at`, then rowid), over the initial values. A `relationship_change` stores its requested delta in
  `delta`; the fold applies what the limits allow given the net change of that dimension so far in the row's run (a
  row without a run counts alone) and on its local day, and keeps the value from 0 to 100. A replay therefore computes
  the limits again without the rows it no longer reads. `value_before` and `value_after` record what the fold gave at
  write time, so the row holds the requested and the applied delta. After every write or invalidation the
  `relationship_states` row is written again from a replay of the rows read back, so it always equals that replay.
- **Limits.** The per-run limit is a net change of ±8 per dimension, the per-day one ±15 per calendar day of
  `LYRVEIL_TZ`, from the row's `created_at`. A `large` event is 8 only when the same run stored or confirmed an episode
  of importance at least 4, otherwise 4. Rows of a run share the commit time, or the newest `created_at` of the chat
  when the clock is behind it.
- **Events from a run.** Each relationship event with its sources in the window is one row with its own reason and
  messages; one with a source outside the window, or a reason that is blank once on one line, is dropped. The
  `StateUpdate` has no sources or reason of its own, so its rows cite every message of the window and carry a fixed
  Italian reason per kind. The emotional state is one `emotion_change` when it differs from the current one; each close
  that matches an open thread is a `thread_closed` naming that thread; each new thread and goal is a `thread_opened` or
  `goal_change`; a new narrative change is a `narrative_change`. Texts are compared as memories are deduplicated (same
  fingerprint, or a stem Jaccard of at least 0.8), and must fit 200 characters on one line. Additions not in the
  documents bound what the model can write: at most 4 items per list per run, and the state keeps the newest 6 open
  threads, 5 goals and 10 narrative changes. Dropped items are counted in the job's `dropped_items`, and its
  `state_events` counts the rows written.
- **Replay after invalidation.** `CharacterStateManager.invalidate(connection, conversation_id, message_ids, now)` marks
  `invalidated` the applied rows that cite one of the messages and stores the replay of the rest. Since P04.7 an edit,
  deletion, regeneration or variant switch of a message calls it in the transaction of the change, and a
  re-evaluation proposes the state of its window again, without counting twice a relationship change a remaining row
  holds or overriding a newer state ([Delivered in P04.7](memory-engine.md#delivered-in-p047)).
- **Isolation.** Every read, write and invalidation names one conversation, and the fold reads one conversation's
  rows, so a change in one chat never reaches another (T-CON-03).
- **Prompt.** A chat with a `relationship_states` row has block 4 ([Block 4](prompting.md#block-4-state-and-time)). The
  table above words the bands from the user's side ("si fida di te"); block 4 speaks to the character, so it uses the
  same phrases turned to the second person with the user's name ("ti fidi di Sara"). A chat whose state was never
  written has no block 4, so the starting values never contradict a scenario that sets none. The extraction prompt
  (`extraction.v3`) lists the open threads, so the model can close one by repeating it.
- **Not delivered.** `current_topic`: no event kind exists for it, so a proposed topic is counted in `deferred_items`
  and the column stays empty; adding one needs a migration of the `event_kind` list, which is open. A scenario's
  starting narrative and values (scenarios have no such fields), the user's corrections and the relationship routes
  (P04.9), and the use of `expectations`, goals and narrative changes in a prompt: block 4's template has no line for
  goals and narrative changes, and nothing writes the narrative, expectations or boundaries yet. `recent_event_ids_json`
  holds the newest 5 active episodes and is not shown anywhere.

`tests/unit/test_character_state.py` holds T-CON-06 and the pure half of T-MEM-23; `tests/integration/test_character_state.py`
holds T-CON-03, the extraction half of T-CON-06 and the database half of T-MEM-23 ([Testing](testing.md#consistency)).

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

Delivered in P03.1b: versions, diff, restore and the locks against proposals
([Versions and locks](character-system.md#versions-and-locks)). Recording the version in each reply is not delivered.

## Reply normalization

`ReplyNormalizer` gives every profile the same surface form
([ADR 0032](decisions/0032-model-independent-reply-normalization.md)). It runs on the streamed text after V2
and V3, and before V4 and the client. Its rules only remove or reshape text, and the only text it adds is
`…` in N4.

| Rule | Effect | While streaming |
| --- | --- | --- |
| N1 | Removes a leading speaker label, the character's first or full name followed by `:` or a dash, and quotes that wrap the whole reply | Holds back the start while it may still be a label or a wrapping quote, up to the first 40 characters |
| N2 | Turns Markdown headings, rules, tables and code fences into plain text. `**bold**` loses its markers | Holds a line start while it may still be Markdown, and a table row until it ends |
| N3 | Applies `narration`. `nessuna` removes asterisk spans of three or more words, `leggera` keeps the first such span, `libera` keeps all. Shorter spans stay as emphasis | Holds back a span that may be removed until it closes |
| N4 | When `finish_reason` is `length`, cuts after the last sentence end if at least half of the text remains, otherwise appends `…` | At the end |
| N5 | Removes emoji when `emoji` is `mai`, then collapses blank lines and trailing spaces | Holds only whitespace that may turn out trailing |

`message_variants.checks_json` records the rules that fired, with counts.

### Delivered in P04.8a

`consistency/normalizer.py` holds `ReplyNormalizer`, and `normalize_reply` for a text known in one piece.
`ChatInferenceService._stream` feeds it every token of a reply, and only what it releases is published or stored
([Streaming persistence](backend.md#streaming-persistence)).

- **Order.** The rules read the card's full and first name, `narration` and `emoji` (`NormalizerRules.for_card`). They
  run as N1, N2, N3, the emoji half of N5, then the spacing half of N5. N4 needs the whole text, so it runs last, on
  the text the others released. Each stage reads its input one character at a time, or decides only on facts that more
  text cannot change, so the delivered text never depends on how the reply was split (T-CON-09).
- **N1.** The name matches in any case, with up to two asterisks around it (`**Nerea:**`), followed by `:`, an en or
  em dash, or `-` and whitespace. After the label, an opening quote (`"`, `“`, `«` or `„`) wraps the reply when its
  closing quote falls within the first 40 characters and only whitespace follows it, or when no closing quote falls
  within them. In that second case the first closing quote that follows goes with it, wherever it is, so the quotes
  left stay paired. A quoted line closed within the 40 characters and followed by more text stays. A reply that
  cannot start with a label or a quote streams at once.
- **N2.** A heading keeps its title, and lines inside a fence stay as written. Rule lines, fence lines and table
  separator rows go with their line. A table row becomes its non-empty cells joined by `, `. A run of asterisks keeps
  one asterisk when its length is odd and none when it is even. Lists, block quotes and inline code stay.
- **N3.** A span is the text between two asterisks on one line, and its words are the whitespace-separated pieces with
  a letter or a digit. An asterisk with no closing one on its line stays as written. Only a span that may be removed
  is held back: every span for `nessuna`, the spans after the first narration span for `leggera`, none for `libera`.
- **N5.** Emoji are the code points from U+1F000 to U+1FAFF and from U+2600 to U+27BF, and a few others such as ⭐.
  Their joiners, selectors and tags go with them. The spacing half drops leading and trailing whitespace and the
  trailing spaces of each line, and keeps at most one blank line in a row. Where text was removed, the gap becomes
  one space, or none before closing punctuation or at the start of a line, and a line that held only removed text
  disappears.
- **N4.** A sentence end is `.`, `!`, `?` or `…`, with any closing quote, bracket or asterisk after it, followed by
  whitespace or the end. Before `…`, trailing commas, colons, semicolons and dashes go. Text already streamed cannot be
  taken back, so when N4 cuts, `message.completed` carries the delivered text and the client replaces the streamed
  text with it.
- **Record.** `checks_json` holds `{"normalizer": {"N1": 1, ...}}`, with a count for each rule that fired: N1 the label
  and the quotes; N2 each heading, rule, table row, fence line and `**` marker; N3 each removed span; N4 its cut or
  ellipsis; N5 each removed run of emoji and each shortened run of blank lines. Since P04.8b the record also holds
  the reply checks and the regenerations ([Delivered in P04.8b](#delivered-in-p048b)).

`tests/unit/test_reply_normalizer.py` holds T-CON-09 and the single rules. `tests/integration/test_reply_streaming.py`
checks that the reply path publishes and stores only normalized text, with its `checks_json`.

## Reply checks

These run on every generated reply. They are deterministic and fast. The lists are stored per
language under `prompts/<language>/`.

### Blocking

| ID | Check | Action |
| --- | --- | --- |
| V1 | Empty or whitespace reply | One retry |
| V2 | Control text of the active template family in the text, such as `<turn|>`, `[INST]` or `<|im_end|>`, or a frame marker of the dynamic context | Stop sequences cut it during streaming. If any remains, remove it and record the event |
| V3 | The reply writes the user's turn, such as a line starting with the user's name and `:` | Stop sequence, then cut at that point. An empty remainder counts as V1 |
| V4 | Content guard ([ADR 0018](decisions/0018-adult-only-characters-and-content-safeguards.md)) | Checked every sentence during streaming. Aborts and replaces the text with a notice |

After a failed retry the message becomes `failed`, and the interface offers a retry. No text is
invented.

### Corrected

These run on each sentence during streaming.

| ID | Check | Languages |
| --- | --- | --- |
| V6 | The character calls itself by another name, such as `mi chiamo` or `sono` followed by a capitalized name other than its own | All supported |
| V10 | The character states another age, such as `ho 31 anni` | All supported |
| V11 | First-person agreement contradicts `grammatical_gender`, such as `sono stanco` for `femminile` or `mi sono alzata` for `maschile`. Skipped for `non_indicato` and inside quotation marks. Closed list in `prompts/it/gender_agreement.txt` | Italian |

A hit aborts the generation, publishes `message.restarted` and generates once more with the matching note
in the [anchor](prompting.md#block-9-anchor). If the second reply hits again, it is delivered and the hit
is recorded.

### Recorded

These do not interrupt the reply. They feed the diagnostic view, the adaptive notes, the
[Italian quality metrics](prompting.md#italian-quality-protocol) and the [consistency protocol](#consistency-protocol).

| ID | Check |
| --- | --- |
| V5 | Talks about being a model or about prompts when the user did not ask |
| V7 | Same first three words as one of the last 10 replies, or similarity of at least 0.9 |
| V8 | Language differs from the expected one, by a stop-word ratio |
| V9 | Contains a phrase from the calque list |
| V12 | Second-person agreement contradicts `user.grammatical_gender`, such as `sei stanca` for a user set to `maschile`. Italian only |
| V13 | Forms of the other address form: `Lei`, `La` and courtesy verb forms for a `tu` character, or `tu`, `ti` and `tuo` for a `lei` character. Italian only |

### Adaptive notes

Two consecutive V7 hits, or one V8, V9 or V13 hit, add the matching note to the anchor of the next 3
replies. At most two notes are active, and a new note replaces the oldest one. Notes are fixed texts, so
they never quote the reply.

### Delivered in P04.8b

`consistency/checks.py` holds the checks, the V2 and V3 cut, the sentence splitter and the adaptive notes, over the
lists of `prompts/<language>/`. `ChatInferenceService` runs them on every reply
([Streaming persistence](backend.md#streaming-persistence)).

- **Order while streaming.** The runtime's text goes through `TurnCutoff` (V2, V3), then the normalizer. The text the
  normalizer releases is cut into sentences: a sentence ends at a line break, or at whitespace after `.`, `!`, `?` or
  `…` and any closing quotes, brackets or asterisks. A sentence is checked as soon as it ends, and the released piece
  that ended it is published only if no check stops the generation. The words of the sentence itself were already
  shown, which is why a stop is announced with `message.restarted`. Every step reads one character at a time, so no
  verdict depends on how the reply was split.
- **Folding and quotations.** Text is compared lowercase and without accents, and every apostrophe reads as `'`. A
  quotation runs from `"`, `“`, `«` or `„` to its closing quote, or to the end of the line. V6 and V10 to V13 skip
  quotations, since they are reported speech. V4 reads them.
- **V1.** A delivered text that is empty, such as one the V3 cut leaves empty, is generated once more from the same
  prompt. It shows no text, so no `message.restarted` is sent. A second empty reply ends `failed` with empty text and
  the job error `REPLY_EMPTY`.
- **V2 and V3.** `TurnCutoff` ends the reply where one of these begins: control text of the template family, `[Contesto
  per` or `[Messaggio di` (V2), or a line that opens with the user's name and `:` (V3). It holds back only an end of the
  text that may still start one. The control texts are the stop sequences plus `<|channel>` and `<channel|>` for
  `gemma4`, and `[/INST]` for `mistral_v3_tekken`. Everything after the cut is dropped, the runtime is asked to abort,
  and the reply ends as if the runtime had stopped there, so N4 does not run. This drops what follows the control text
  instead of keeping it, because control text ends a turn.
- **V4.** `content_guard.find_reply_violation` refuses a sentence that holds a sexual term together with a minor term,
  a child's costume or act, a childhood reference or an age under 18. A minor term alone passes, since a character may
  speak of a child. A hit stops any generation. The reply ends `failed` with the fixed notice "Questa risposta è stata
  bloccata: accostava minori e contenuti sessuali." and the job error `CONTENT_POLICY_BLOCKED`. The client first gets
  `message.restarted`, which drops the streamed words, then `message.updated` with the notice.
- **V5.** A phrase of `model_talk.txt` (`[talk]`) is a hit, unless the user's message holds one of `[talk]` or
  `[asked]`. Only Italian and English have the list.
- **V6.** A phrase of `self_name.txt`, such as `mi chiamo` or `sono`, followed by a capitalized word that is not a word
  of the character's full name or of `[not_names]`. A phrase after a negation (`non sono Luca`) does not count. English
  leaves out `I'm`, French `je suis`, and German `ich bin`, because a capitalized nationality or noun follows them.
- **V7.** The same first 3 folded words as one of the character's last 10 replies, or a `difflib` ratio of at least 0.9
  between the word lists.
- **V8.** In a reply of at least 8 words, more words are stop words of another supported language than of the chat's
  language. The stop words are those of the memory index (`memory/stopwords/`).
- **V9.** A phrase of `prompts/it/calques.txt`. `^` marks an opening, which counts only at the start of the reply, and
  `+` a phrase that counts only when one of the last 10 replies holds it too (`sono qui per te`). The character-break
  phrases of the calque table are V5's.
- **V10.** A phrase of `self_age.txt`, such as `ho {n} anni`, with an age other than the card's, in digits or, in
  Italian, in words (`trentun`). A word of `[not_before]` right after it (`ho 5 anni di esperienza`) means a duration.
- **V11 and V12.** A lead of `gender_agreement.txt` (`sono`, `mi sono` and others for V11; `sei`, `ti sei` and others
  for V12), at most one adverb, then a stem ending in `o` or `a` that contradicts the gender. Plural endings and words
  that are also adverbs (`solo`, `certo`) are left out. V12 reads `user.grammatical_gender`.
- **V13.** The forms of `address_forms.txt`. For a `tu` character, `Lei`, `Suo`, `Sua`, `Suoi` and `Sue` count only
  capitalized and inside a sentence, and courtesy forms such as `mi scusi` count anywhere. For a `lei` character,
  `tu`, `ti`, `te`, `tuo` and imperatives such as `dimmi` count. `La` and `Le` are left out, because a place such as
  `La Spezia` would count.
- **Corrective generation.** In the first generation, V6, V10 or V11 stops the stream: the runtime is asked to abort,
  the stored text is cleared, `message.restarted` names the check, and the prompt is built again with the check's note
  first in the anchor, then the adaptive notes, two at most. The second generation is stopped only by V4, so its V6,
  V10 or V11 hits are delivered and recorded. A run makes at most three generations: the first, one corrective
  generation and one V1 retry.
- **Adaptive notes.** `load_context_request` reads the `checks` of the chat's last 4 replies before the reply
  (`complete` or `interrupted`, with text, active variant) and derives the active notes from them
  (`active_notes`). Nothing is kept in memory, so the notes survive a restart, and a regenerated reply gets the notes
  its first variant had. Notes started by the same reply are taken in the order V7, V8, V9, V13. The texts are in
  `prompts/it/notes.txt`, the same for every chat language, since every prompt block is Italian.
- **Record.** `checks_json` is `{"normalizer": {...}, "checks": {"V9": 1, ...}, "restarts": ["V6"],
  "regenerations": 1}`. `checks` counts each check that fired on the delivered text, V2 and V3 included. `restarts`
  names the check that discarded each earlier generation (`V1`, `V6`, `V10` or `V11`), and `regenerations` is their
  number. A failed reply records the same, with `{"V4": 1}` for V4.

`tests/unit/test_reply_checks.py` holds T-CON-08. `tests/integration/test_reply_streaming.py` holds T-CON-10, with the
V1 and V4 endings.

## Consistency protocol

T-QLT-05 ([ADR 0034](decisions/0034-character-consistency-protocol-and-verified-profiles.md)). Scripts are in
`tests/quality/consistency/`. P04 runs it on every catalog text profile, and P08 runs it again on the default.
Each of the three demo characters runs a scripted conversation of 120 messages, including time jumps,
corrections and a long topic change.

| Check | Target | Hard |
| --- | --- | --- |
| C1. Identity probes: 4 questions at each of messages 10, 60 and 120, so 12 per character. They cover name, age, occupation, place, gender, a main trait and a fact from block 2, scored with keyword lists | At least 95 % correct, and no wrong name or age delivered | Yes |
| C2. V11 hits delivered after the corrective generation | At most 1 per 200 replies | Yes |
| C3. V13 hits | At most 2 per 100 replies | No |
| C4. Median reply length per band, and replies cut by N4 | Median within the band target, at most 10 % cut | Yes |
| C5. Narration spans delivered for `nessuna` | 0. The raw tendency of the model is recorded | No |
| C6. A profile change at message 60 | C1 and C4 still met after the change | Yes |
| C7. Owner's blind test: which character wrote each of 20 replies | At least 80 %. Run on the default and the two roleplay profiles | No |

Each catalog profile gets one tuning round: first templates and examples, then samplers. A profile that
still misses a hard target leaves the catalog through a new ADR. Its file can still be registered as a
custom profile. The results go to `docs/verification/`, and the catalog's `quality_evidence` points to them.

## Extraction evaluation

T-QLT-06. Scripts are in `tests/quality/extraction/`. P04 runs them on every catalog text profile, with 40
synthetic message windows and their expected items.

| Measure | Target |
| --- | --- |
| Expected items found, with the right kind and provenance | At least 85 % |
| Inferences stored as `stated` | At most 3 % |
| Changes to canonical data outside `identity_candidate` | None |
| Valid output after the repair attempt | At least 98 % |

A profile that misses one of these targets after the tuning round leaves the catalog in the same way.

## Tests

See [Testing](testing.md#consistency). The key cases use several characters and check that:

- a fact told to one character is absent from another's context;
- a state change in one chat leaves an independent chat unchanged;
- canonical data is unchanged after long conversations, extraction and summaries;
- the normalizer, the length bands and the corrected checks give the same delivered text for fixed raw
  replies, whatever the template family (T-CON-09 to T-CON-11).
