# ADR 0037: Content guard rules for character text

Status: accepted (2026-11-07). Supersedes: none.

## Context
- [ADR 0018](0018-adult-only-characters-and-content-safeguards.md) requires deterministic Italian and English lists that refuse minors in character data, at the API, the database, import and drafts, and settings cannot disable them. It does not say what counts as describing a minor.
- Adult characters often mention their own childhood ("Da bambina sognava il mare", "At 16 she left home"), and the assisted draft writes such biographies on its own. A list that refuses every minor word refuses all of them, and a refused draft fails after its one repair.
- Italian states durations with the words of an age: "ha 16 anni" is an age, while "da 16 anni", "16 anni fa" and "ha 3 anni di esperienza" are not.
- The negative prompt of a card is where safety terms such as `child` belong.

## Decision
- **Three rules per sentence**, on text read without case, accents or invisible characters, in `lyrveil.consistency.content_guard`:
  - a minor term, a word or phrase that names a minor or presents someone as one, or a child's costume or act, is refused;
  - an age from 0 to 17 stated as an age (`ha 16 anni`, `sedicenne`, `16 years old`) is refused, while durations and age differences pass;
  - a reference to childhood in the same sentence as a sexual term is refused.
- **The character's own past passes in a fixed form.** Phrases such as `da bambina`, `quando era ragazzina`, `a 16 anni` and `as a child` are taken out before the first two rules. The third rule still applies to them.
- **Every stored card is checked.** `validate_card` checks it, and `CharacterService` checks it again before each write, so restore and duplicate refuse an old card that a newer list refuses. Every string of the card is checked except `visual.persistent_negative_en`.
- **One error code.** `422 CONTENT_POLICY_BLOCKED`, with the path of the field, at every entry point, import included. The age rule keeps its schema errors (`VALIDATION_ERROR`, `IMPORT_INVALID`) and the database `CHECK`.
- **Drafts.** The description is checked before the job is queued and again before the model runs. A refused output gets the one repair of [ADR 0013](0013-structured-output-grammar-and-validation.md), and a second refusal fails the job with `CONTENT_POLICY_BLOCKED`.
- The lists are constants of the module. No setting, environment variable or file reaches them.

## Alternatives
- **Refuse every minor word.** Simplest, but it refuses most adult biographies and many drafts.
- **Refuse only text about the character itself.** That needs parsing that term lists cannot do, and a minor elsewhere in the card, such as a daughter of 8, should be refused too.
- **`IMPORT_INVALID` for a refusal at import.** It would mix a safeguard with format errors; one code at every entry point tells the interface why.
- **Lists in a data file.** A file looks like configuration and can be edited in an installation; constants keep the lists code, reviewed and tested.

## Consequences
- Some innocent text is refused: a teacher's `i bambini`, children's books, `in her teens`. A birth year is not read as an age, and a phrase outside the lists passes. Each new list entry comes with a test case.
- The childhood phrases are a deliberate gap: a minor described in one of those past forms passes when no sexual term is in the same sentence.
- The generated-text row of the safeguards (P04) and the image rows (P05) are planned and not covered here.
