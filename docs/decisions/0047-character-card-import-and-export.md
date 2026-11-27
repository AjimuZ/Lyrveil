# ADR 0047: Character Card V1, V2 and V3 import and export

Status: accepted (2026-11-27). Supersedes: the Character Card V2 part of [ADR 0021](0021-character-format-and-card-v2.md). The `lyrveil.character` format of ADR 0021 stays the native exchange format. Planned for version 1.1, phase P10.

## Context
- ADR 0021 deferred Character Card V2 to after 1.0, as a one-way JSON import with no PNG and no export. On 2026-11-27 the owner asked for import of V2 and V3 cards, as JSON and as PNG, and for export too, without losing the original information.
- The specifications were read on 2026-11-27: V2 in `spec_v2.md` of `malfoyslastname/character-card-spec-v2`, and V3 in `SPEC_V3.md` of `kwaroran/character-card-spec-v3`. The facts this decision relies on:
  - **V2.** The envelope is `spec` `"chara_card_v2"`, `spec_version` `"2.0"` and `data`. `data` holds:
    - the texts `name`, `description`, `personality`, `scenario`, `first_mes` and `mes_example`;
    - `creator_notes`, which must not reach prompts;
    - `system_prompt` and `post_history_instructions`, which replace the user's settings by default;
    - `alternate_greetings`;
    - an optional `character_book`, which must be used by default;
    - `tags`, `creator`, `character_version` and `extensions`. Editors "MUST NOT destroy unknown key-value pairs" of `extensions`.
  - **V3.** The envelope is `"chara_card_v3"` and `"3.0"`. V3 adds `assets`, `nickname`, `creator_notes_multilingual`, `source`, `group_only_greetings`, `creation_date` and `modification_date`. Lorebook entries gain `use_regex` and `@@` decorator lines. V3 also defines `{{char}}`, `{{user}}` and random-value macros.
  - **Embedding.** A V3 card sits in a PNG or APNG `tEXt` chunk named `ccv3`, as the base64 of the UTF-8 JSON. A reader may read a V2 card from a `chara` chunk and should prefer `ccv3` when both are present. CHARX is a zip archive with `card.json` at its root.
  - **V1.** Per the V2 README, a V1 card has the six texts at the top level, with no envelope.
- A Lyrveil card is structured: traits, a communication profile and a visual identity ([Character system](../character-system.md#card-schema)). A community card is free text, usually in English, and it often states no age.
- SillyTavern is AGPL-3.0 and Lyrveil is Apache-2.0 ([R-PRD-06](../requirements.md#1-product)). The format is implemented from the specifications only. No SillyTavern code is reused.

## Decision
- **What is read.** Lyrveil reads V1, V2 and V3 cards as JSON, and V2 and V3 cards embedded in PNG or APNG. The format comes from the content, not from the file extension. When a PNG holds both chunks, `ccv3` wins. The file limit stays 64 MB. The image is decoded by Pillow with the upload limits, and the `tEXt` chunks are read by Lyrveil's own length- and CRC-checked reader, so no global Pillow limit changes. CHARX is not read in P10.
- **Nothing is lost.**
  - The parsed card is stored unchanged in `character_sources`, together with the file's SHA-256, its format and the import time. **Scarica la card originale** returns it as it was imported.
  - Remote `uri` values are never fetched. Data URIs stay inside the stored JSON.
- **Every import becomes a draft that the user reviews.** No import writes a character directly. `POST /characters/imports` parses the card, checks it and stores a `card_imports` row with two possible drafts:
  - **Direct mapping.** It is deterministic and needs no model. It follows the [mapping table](../character-system.md#character-cards). The text keeps its source language.
  - **Structured conversion.** This is an optional `card_conversion` job, started by **Struttura con il modello**. It reuses the grammar, validation and single repair of assisted creation ([ADR 0013](0013-structured-output-grammar-and-validation.md)). The model reads the card text as data and writes a `CharacterDraft` in the character's language, Italian by default, keeping proper names. The job is refused with `CARD_TOO_LONG` when the card text, counted by the runtime, leaves less room than the draft's `max_length` plus the template.

  Either draft opens in the Creator with its fields marked **dalla card** or **proposto**. **Salva personaggio** is the normal `POST /characters` with `import_id`, so every card rule applies. Version 1 has the actor `import`.
- **Age.** The age is never defaulted.
  - The Creator leaves it empty and required.
  - It is filled only when the card text states an adult age in an explicit form (`27 years old`, `27 anni`, `age: 27`), and the field is then marked **dalla card**.
  - An age written by the conversion model is not used.
- **Content guard.** Every string of the card is checked by the content guard of [ADR 0037](0037-content-guard-rules-for-character-text.md), including unused fields and lorebook entries, but not data URIs. A refusal answers `422 CONTENT_POLICY_BLOCKED` with the source path, such as `data.character_book.entries[3].content`, and stores nothing.
- **Placeholders.**
  - **Kept.** `{{char}}` and `{{user}}` stay in the stored text. They are matched without case, and the legacy `<BOT>`, `<USER>` and `<char>` become these two. The prompt renderer replaces them with the character's first name and the user's display name, which is the persona's name from P11 ([ADR 0048](0048-user-personas.md)). Card texts written by hand may use them too.
  - **Removed or replaced.** `{{// …}}`, `{{comment: …}}` and `{{hidden_key: …}}` are removed. `{{random: …}}` and `{{pick: …}}` keep their first option, `{{reverse: …}}` is evaluated, and `{{roll: …}}` and any other `{{…}}` are removed.
  - **Reported.** Each replacement is listed in the import report, so the reply prompt stays deterministic and the stable prefix stays stable ([ADR 0031](0031-stable-prefix-dynamic-context-and-anchor.md)).
- **Fields that never reach a prompt.** `system_prompt`, `post_history_instructions`, `creator_notes` (and its multilingual form), `tags`, `creator`, `character_version`, `source` and the dates are kept and shown in the preview and the profile. Block 1 always comes first, and imported text cannot replace it ([ADR 0018](0018-adult-only-characters-and-content-safeguards.md)). This is a deliberate departure from the V2 default for `system_prompt` and `post_history_instructions`.
- **Greetings become scenarios.** `first_mes` with `scenario` becomes a [scenario](../character-system.md#conversations-and-scenarios) for the character, and each `alternate_greetings` entry becomes another scenario with the same description. The preview has this option on by default. `group_only_greetings` is kept, because Lyrveil has no group conversations.
- **Lorebook.** `character_book` is kept in the source. From P12 it becomes the character's own lore book ([ADR 0049](0049-worlds-and-lore-entries.md)), and P12's migration converts the books of characters imported earlier.
- **Avatar.** For a PNG card, the image becomes the primary avatar through the upload path. The re-encode removes every chunk. For a JSON card, a V3 `icon` asset named `main` with a data URI becomes the avatar. Without either, the character has no avatar.
- **Export.** `GET /characters/{id}/card` takes `format` `v3json` (the default), `v2json` or `png`.
  - **The texts.** The free text is rendered deterministically from the current card, with the texts of prompt blocks 2 and 3 and the Italian visual fields:
    - `description` holds the identity and the look;
    - `personality` holds the traits;
    - `mes_example` holds the examples as `<START>` blocks of `{{user}}:` and `{{char}}:` lines;
    - `scenario`, `first_mes` and `alternate_greetings` come from scenarios that the user picks for the export;
    - `system_prompt` and `post_history_instructions` are empty;
    - `character_book` is the own lore book from P12.
  - **The native card.** `extensions.lyrveil` holds `{"format": "lyrveil.character", "format_version": 1, "card": {…}, "text_sha256": "…"}`: the complete card, and the hash of the rendered free text.
  - **What an imported character keeps.** When the character came from a card, the export keeps from the stored source every `extensions` key other than `lyrveil`, and `tags`, `creator`, `creator_notes`, `source`, `character_version` and `creation_date`. `modification_date` is the time of the export. A character without a source has an empty `creator`, its version number as `character_version`, and its `created_at` as `creation_date`.
  - **PNG.** The PNG is the primary avatar, re-encoded as PNG, with a `ccv3` chunk and a V2 `chara` chunk for readers without V3. A character without an avatar exports JSON only.
  - **What is never exported.** Conversations, memories, personas, other images and the user's name.
- **Round trip.** On import, a card with `extensions.lyrveil` whose `text_sha256` matches its free text is imported from the embedded card, with the rules of the native format, so Lyrveil, then a card, then Lyrveil loses nothing. When the hash differs, the text was edited elsewhere. The preview then names the changed fields and preselects the embedded card, and **Usa i testi modificati** switches to the direct mapping.
- **Code.** `lyrveil.domain.tavern_card` holds the models and the parser, `lyrveil.services.card_import` and `lyrveil.services.card_export` hold the flows, and the routes are in `lyrveil.api.characters`.

## Alternatives
- **Import only, as in ADR 0021.** The owner asked for export.
- **Export without the embedded Lyrveil card.** A round trip would flatten every structured field into free text.
- **Use `system_prompt` and `post_history_instructions` as the card asks.** Imported text would then displace the global instructions and their safeguards.
- **Default the age to 18, or accept the model's age.** Either hides missing data on the one rule that must never be guessed.
- **Run the structured conversion on every import.** It spends GPU time without being asked, and it needs the text model loaded. The direct mapping is always available offline.
- **Read CHARX in P10.** It brings zip handling, asset paths and more attack surface, and few characters exist only as CHARX. It is a later candidate.
- **Reuse SillyTavern's importer.** Its AGPL-3.0 license does not fit an Apache-2.0 project.

## Consequences
- Lyrveil reads community cards and writes cards that other applications read. A Lyrveil round trip is lossless. In a card, then Lyrveil, then a card, unknown fields survive, but the free text is rendered again from the Lyrveil card. The original wording stays available as the stored source.
- These departures from the specifications are deliberate and documented:
  - `system_prompt` and `post_history_instructions` are not applied;
  - `character_book` is unused until P12;
  - alternate greetings become scenarios instead of swipes;
  - CHARX, `use_regex` entries and recursive lorebook scanning are not supported.
- A long card can exceed the room of blocks 2 and 3. The preview shows the runtime token count when the text model is loaded, and otherwise a labelled estimate that only warns. Shortening is left to the user or to the structured conversion.
- Tests T-CRD-01 to T-CRD-09 ([Testing](../testing.md#character-cards)) verify this decision, and P10 in the [roadmap](../roadmap.md#p10-character-cards) delivers it. Until P10 runs, none of this is built.
