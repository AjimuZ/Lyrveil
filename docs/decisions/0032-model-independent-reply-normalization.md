# ADR 0032: Reply normalization, length bands and identity checks independent of the model

Status: accepted (2026-11-06). Supersedes: none.

## Context
- Profiles bring their own habits, and words in the card alone cannot override them. As a result, the same character reads differently on each profile:
  - Instruct models tend toward Markdown and long answers.
  - Roleplay finetunes tend toward narration between asterisks, a speaker label such as `Nerea:` and quotes around the whole reply.
- `message_length` reaches the prompt only as a word, and every reply gets `max_length` 400. That causes two problems:
  - A model that writes long is cut mid-sentence (`finish_reason` `length`).
  - A character meant to be brief still has room for a page.
- Only validators V1 to V4 change the reply. A wrong name (V6) is only recorded. Nothing checks:
  - the stated age;
  - Italian gender agreement;
  - the choice between *tu* and *Lei*.

## Decision
- **Length bands.** `message_length` sets `max_length` and the anchor's length text. `llm.max_length`, default 400, caps every band. Target medians are in tokens.

  | Band | `max_length` | Target median | Anchor text |
  | --- | --- | --- | --- |
  | `brevissimi` | 60 | At most 30 | Rispondi con una frase sola, brevissima. |
  | `brevi` | 120 | At most 70 | Rispondi in una o due frasi brevi. |
  | `medi` | 240 | At most 160 | Rispondi in poche frasi. |
  | `lunghi` | 400 | At most 300 | Puoi scrivere un messaggio lungo, se serve. |

- **`ReplyNormalizer`** runs on the streamed text, after V2 and V3 and before anything reaches the client. Its rules are deterministic, and they only remove or reshape text. The only text it adds is `…`, in N4.
  - **N1** removes a leading speaker label, meaning the character's first or full name followed by `:` or a dash. It also removes quotes that wrap the whole reply. The first 40 characters are held back until N1 decides.
  - **N2** turns Markdown headings, rules, tables and code fences into plain text, and removes the markers of `**bold**`. Single `*emphasis*` is left to N3.
  - **N3** applies the card's `narration` rule ([ADR 0033](0033-card-fields-for-a-model-independent-voice.md)):
    - `nessuna` removes every asterisk span of three or more words. During streaming, a span is held back until it closes.
    - `leggera` keeps the first span and removes later ones.
    - `libera` keeps every span.

    Shorter spans count as emphasis and always stay.
  - **N4** handles `finish_reason` `length`. If at least half of the text remains after the last sentence end, it cuts there. Otherwise it appends `…`.
  - **N5** removes emoji when the card's `emoji` value is `mai`, then collapses runs of blank lines and trailing spaces.
- **Corrected checks.** These run on each sentence during streaming:
  - **V6**: the character calls itself by another name.
  - **V10**: the character states another age, such as `ho 31 anni`.
  - **V11**: first-person gender agreement differs from the card's `grammatical_gender`, such as `sono stanco` for a `femminile` character. It uses a closed list of patterns, skips text inside quotation marks, and runs for Italian only.

  A hit stops the stream and starts one new generation, with a corrective note in the anchor. The client receives `message.restarted` and clears the bubble. If the second reply hits again, it is delivered and the hit is recorded.
- **Recorded checks**, Italian only:
  - **V12**: second-person agreement with the user's `grammatical_gender`.
  - **V13**: the address form, such as `Lei` forms for a character set to `tu`.
- **Adaptive notes.** Two consecutive V7 hits, or one V8, V9 or V13 hit, add a fixed corrective note to the next anchor. At most two notes are active at a time, and each one lasts 3 replies.
- Lists and notes live under `prompts/<language>/`. V6 and V10 have patterns for every supported language. V11 to V13 run only for Italian.
- `message_variants.checks_json` records the rules and checks that fired, and the regeneration count.

## Alternatives
- **Prompt instructions alone.** Profiles follow such rules unevenly, which is the problem being solved.
- **A grammar for chat replies.** GBNF can forbid asterisks or Markdown, but it constrains the sampling of every token, and it cannot express gender agreement.
- **A second model call that rewrites the reply.** It doubles the time and can change the meaning.
- **Blocking on V12 and V13.** The patterns have false positives in reported speech. A hit is recorded and corrected through a note instead.

## Consequences
- The same card gives the same surface form on every profile: no labels, no Markdown, the chosen narration and a predictable length.
- A V6, V10 or V11 hit costs one more generation.
- T-CON-09 to T-CON-11 cover the rules with fixed texts. The [consistency protocol](0034-character-consistency-protocol-and-verified-profiles.md) measures how often they fire on each profile.
- Streaming starts up to 40 characters later, and narration spans appear only once they close.
