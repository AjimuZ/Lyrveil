# ADR 0033: Card fields for a model-independent voice

Status: accepted (2026-11-06). Supersedes: none.

## Context
- Italian marks gender in adjectives and participles, both for the speaker and for the person addressed. The card has no grammatical gender, only `subject_tag` for images, and the user has none. Each model guesses from the names, and the guesses differ.
- `formality` describes a register. Choosing *tu* or *Lei* is a separate decision, and it must not change between replies.
- Narration and emoji follow the model's habits unless the card fixes them.
- `examples` holds isolated lines. A line shown together with the message it answers teaches turn-taking and register better.
- The global rules ban some formulas, but a character cannot add its own.
- Version 1 of the exchange format has not been released, so it can still change without a new version number.

## Decision
- **`Identity.grammatical_gender`**: `femminile`, `maschile` or `non_indicato`, default `non_indicato`. It is stored in `characters.grammatical_gender`, with a CHECK constraint. Block 2 and the anchor say "Parli di te al femminile" or "al maschile". With `non_indicato`, the line is omitted and V11 does not run.
- **The setting `user.grammatical_gender`**, with the same values and the same default. Block 1 says "Quando ti rivolgi a {{user}}, usa il femminile" or "il maschile".
- **`CommunicationProfile`** gains these fields:
  - `address_form`: `tu` or `lei`, default `tu`. The Creator proposes `lei` when `formality` is `formale`.
  - `narration`: `nessuna`, `leggera` or `libera`, default `nessuna`.
  - `emoji`: `mai`, `raramente`, `a_volte` or `spesso`, default `raramente`.
  - `examples`: up to 8 `ExampleExchange` objects. Each one has an optional `user` line of up to 300 characters and a `character` line of up to 500. Block 3 renders them as a short dialogue headed "Esempi di stile, non cose successe", in saved order, and stops before the first example that would take the block over its maximum of 500 tokens. The editor shows how many examples reach the prompt.
  - `avoid`: up to 8 phrases of up to 120 characters each, rendered as "Non diresti mai: …".
- Enum values reach templates through a `words` filter that replaces `_` with a space, so `molto_informale` reads as `molto informale`.
- While `subject_tag` still has its default value, the Creator suggests a tag that matches `grammatical_gender`.
- Version 1 of the exchange format includes the new fields. The demo characters use them:

  | Character | `grammatical_gender` | `address_form` | `narration` | `emoji` |
  | --- | --- | --- | --- | --- |
  | Nerea | `femminile` | `tu` | `nessuna` | `raramente` |
  | Ilario | `maschile` | `lei` | `leggera` | `mai` |
  | Tessa | `femminile` | `tu` | `nessuna` | `spesso` |

## Alternatives
- **Infer gender from the first name or from `subject_tag`.** Names are ambiguous, and `1other` says nothing about grammar.
- **A free-text style field for narration and emoji.** The normalizer of [ADR 0032](0032-model-independent-reply-normalization.md) needs a closed value to enforce.
- **Keep isolated example lines.** They are simpler to type, but they show turn-taking poorly.

## Consequences
- P03 changes the card, its schema, the Creator, the exchange format, the demo fixture and `characters` (T-CHR-14). Nothing is stored yet, so no data needs migrating.
- The anchor and the normalizer read these fields on every reply, so the voice no longer depends on how a model reads the card.
