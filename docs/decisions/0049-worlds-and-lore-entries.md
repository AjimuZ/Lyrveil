# ADR 0049: Worlds and lore entries activated by keywords

Status: accepted (2026-11-27). Supersedes: none. Planned for version 1.1, phase P12.

## Context
- On 2026-11-27 the owner asked for lore: reference text about places, people, families and events that enters a reply only when the conversation touches it. It comes in three levels: one character's lore, a world's lore shared by several characters, and the lore of one conversation. Lore has keywords, contextual activation, priorities and a token limit.
- Lyrveil already has related parts, and none of them is lore:
  - the card is the character's canonical identity;
  - memories are extracted from messages, carry provenance and confidence, and belong to one character ([Memory layers](../memory-engine.md#memory-layers));
  - a scenario is copied into a conversation at its start.

  Lore is written by the user, stays fixed until the user edits it, and can be shared across characters. No current table fits it.
- Character Cards carry a `character_book`, and V3 defines a standalone `lorebook_v3` file ([ADR 0047](0047-character-card-import-and-export.md)).
  - An entry has `keys`, `content`, `enabled` and `insertion_order`, and optionally `secondary_keys` with `selective`, `constant`, `case_sensitive`, `priority`, `name`, `comment`, `id` and `position`.
  - A lower `priority` is discarded first when the budget is reached, and a lower `insertion_order` is inserted earlier.
  - V3 adds `use_regex` and `@@` decorator lines.
- Constraints:
  - No embedding model ([R-MEM-17](../requirements.md#8-memory-lyrveil-memory-engine), R-MOD-06).
  - The stable prefix must stay byte-identical between replies ([ADR 0031](0031-stable-prefix-dynamic-context-and-anchor.md)).
  - Text that reaches a prompt passes the content guard ([ADR 0037](0037-content-guard-rules-for-character-text.md)).
  - The catalog profiles run at a 12,288-token context.

## Decision
- **Books and entries.**
  - A **lore book** (`lore_books`) is either a character's own book or a **world**. A character has at most one own book.
  - A world has a name, a description that never reaches a prompt, and the status `active` or `archived`.
  - Every book has `scan_depth`, from 1 to 20 messages with 4 as the default, and an optional `token_budget`, from 50 to 900, which caps that book's activated entries.
- **An entry** (`lore_entries`) has these fields:
  - `title`, up to 80 characters;
  - `content`, up to 8,000 characters;
  - `keys`, `secondary_keys` and `exclude_keys`, up to 16 each, each up to 60 characters;
  - `case_sensitive`, `constant` and `enabled`;
  - `priority`, an integer;
  - `insertion_order`, an integer;
  - `note`, up to 500 characters, shown only in the editor.

  Titles and contents pass the content guard with the card rules, so a minor described anywhere in lore is refused, as anywhere in a card. An entry whose rendered text is over its block's maximum is stored, but never enters a prompt, and the editor says so.
- **Links decide which books a reply reads.** A world is linked to characters, scenarios or single conversations (`lore_links`, one target per row). A reply reads three kinds of books:
  - the character's own book;
  - the worlds linked to the character, which apply to every conversation of that character;
  - the worlds linked to the conversation. Starting a conversation from a scenario copies the scenario's links into the conversation; later scenario edits do not change it.

  There is no separate conversation lore: a world linked to one conversation is that level. Lore never crosses into a conversation by any other path.
- **Activation is deterministic and needs no model.**
  - **Scan window.** It is the newest `scan_depth` messages of the conversation, the current message included, from both sides.
  - **Key stems.** Keys are stemmed when they are saved, with the conversation's language and the steps of [Stemming](../memory-engine.md#stemming).
  - **A key matches** when its stem sequence appears contiguously in the stem sequence of the window. A `case_sensitive` key matches its exact text at word boundaries instead.
  - **An entry is active** when it is `enabled` and any key matches. With `secondary_keys`, a secondary key must also match. Any matching `exclude_keys` deactivates it.
  - **Constant entries** are always active, and they need no key.
  - **Refused keys.** A key with no stem left after stop-word removal, such as `lui`, is refused when saved.
  - **Not supported.** Recursive activation and regular-expression keys are not supported.
- **Placement keeps the prefix stable.**
  - **Block 11** holds the constant entries, in the stable text after block 10. Its budget is 0 tokens minimum, 300 as the target and 600 at most.
  - **Block 12** holds the activated entries, in the dynamic context after block 5 and before the anchor. Its budget is 0 tokens minimum, 500 as the target and 900 at most.
  - Block 11 changes only when the user edits constant entries or links, and block 12 changes with the conversation and never touches the prefix ([Prompting](../prompting.md#blocks-10-to-12)).
- **Selection and order.**
  - **What is kept.** Within each block, entries are kept by `priority`, highest first. On a tie the character's own book comes before worlds, and then the lower `insertion_order` comes first. Entries are added until the block's budget or the book's `token_budget` is reached.
  - **How they are rendered.** Kept entries are shown in that order, own book first, as `- {title}: {content}`, or as `{content}` alone when there is no title. The `{{char}}` and `{{user}}` placeholders are replaced as in cards.
  - **Over the whole prompt's budget.** Activated entries are cut after the oldest summary parts and before the lowest-scoring memories. Constant entries are cut after the optional identity fields and the persona text ([Context composition](../memory-engine.md#planned-for-version-11)).
- **Precedence in block 1.** Version 2 of the global instructions adds the world to what the character knows. The order is: the card first, then a `[sicuro]` memory (a later event in this chat overrides the world), then lore, then `[incerto]` memories.
- **Lore stays out of memory.** Extraction reads messages only, so lore never becomes a memory, and memories never edit lore.
  - Each assembly records `lore_refs`: the entry, its book, the hash of its content, the key that matched, the block, and whether it was kept or cut.
  - Diagnostic mode lists the entries under **Voci di lore**, apart from the memories.
- **Import and export.**
  - A card's `character_book` becomes the character's own book. P12's migration converts the books stored by P10's imports.
  - A standalone `lorebook_v3` file imports as a world, and a world or an own book exports as `lorebook_v3`.
  - Field mapping: `name` becomes `title`, `comment` becomes `note`, and `selective` decides whether `secondary_keys` apply. The other fields keep their names.
  - Decorators: `@@activate` sets `constant`, `@@dont_activate` clears `enabled`, `@@additional_keys` adds keys and `@@exclude_keys` fills `exclude_keys`. These lines leave the content.
  - Entries with `use_regex: true` are imported disabled, and so are entries left with no usable key that are not constant. Each one is listed in the import report.
  - `id`, `position`, `extensions`, `recursive_scanning`, the `use_regex` flag and the decorators Lyrveil does not apply are kept in `source_json`. Export writes them back, with the decorators that were not applied restored as lines at the start of the content. SillyTavern's own world-info files are a later candidate, after their format is checked.
- **Interface.**
  - **Mondi** (`/worlds`, `/worlds/:id`) lists worlds and edits entries and links.
  - The character's own book opens from the profile at `/characters/:id/lore`.
  - **Prova le parole chiave** runs the matcher on a pasted text, with no model, and shows which entries would be active and why.

## Alternatives
- **Lore as shared memories.** Memories are extracted, labelled by confidence and owned by one character. Lore written by the user and shared by many characters would blur that provenance ([ADR 0012](0012-memory-provenance-and-history.md)).
- **Semantic activation with embeddings.** It needs another model, and R-MOD-06 excludes one. Keywords are predictable, and the user can test them in the editor.
- **An FTS5 table for keys.** A conversation reads at most a few books of up to a few hundred entries. Matching stems in Python is fast enough, and it is easier to explain in the editor.
- **Regular-expression keys.** Python's `re` has no timeout, so an imported pattern could stall the worker.
- **Recursive activation.** It makes the selection hard to predict, and the budget is small anyway.
- **Activated entries in the stable text.** Each change of keywords would move the cache point before the history, so the history would be processed again on most replies.
- **A third, conversation-only level of lore.** Scenarios, conversation memories and worlds linked to one conversation already cover it.

## Consequences
- Characters that share a world agree on its facts, and a reply reads only the lore that its conversation touches. A later group conversation can read the union of its members' links.
- The prompt has three more blocks. With the targets of blocks 1 to 12 at a 12,288-token context, about 7,600 tokens remain for messages; at 8,192, about 3,500 remain. On small contexts, lore can push memories out sooner, and the reduction order above decides what goes first.
- These parts of the card specifications are not supported: regular-expression keys, recursive scanning, entry `position`, and most V3 decorators. Imports keep them so that exports do not lose them.
- Tests T-LOR-01 to T-LOR-08 ([Testing](../testing.md#lore)) verify this decision, and P12 in the [roadmap](../roadmap.md#p12-worlds-and-lore) delivers it. Until P12 runs, none of this is built.
