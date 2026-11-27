# ADR 0048: User personas, one per conversation

Status: accepted (2026-11-27). Supersedes: none. Replaces the settings `user.display_name` and `user.grammatical_gender` ([Configuration](../configuration.md#application-settings)) with the default persona. Planned for version 1.1, phase P11.

## Context
- Today the user is two settings, a display name and how characters address them. No route or screen sets them yet, so every prompt reads their defaults, `Utente` and `non_indicato` (`UserProfile` in `lyrveil.context.minimal`). Every character in every conversation sees the same user. On 2026-11-27 the owner asked for several identities, for example one for a fantasy story and one for a story set in real life. Each identity has a name, an age, an appearance, a personality, a job, interests, a relationship with each character and what the user wants to share.
- The owner also asked that each character know only what it has actually been told. The memory engine already enforces this for conversations: a fact about the user needs a source message in that conversation ([Consistency rules](../memory-engine.md#consistency-rules)), and conversations stay isolated (R-MSG-11, R-CON-02).
- A shared memory can be read by any conversation of the same character that has `shares_memory = 1` ([Shared memory](../memory-engine.md#shared-memory)). With personas, a fact about one identity could reach a conversation with another one.
- The user's name and gender appear in block 1, in block 3, in the anchor, in the stop sequences, in the reply checks and in the extraction and proactive prompts ([Prompting](../prompting.md#system-blocks)).
- Each conversation has a single character today. A later group conversation would still have one user.

## Decision
- **A persona** is a row of `user_personas`. Its fields are:
  - `name`, 1 to 40 characters;
  - `grammatical_gender`, with the values of the setting it replaces;
  - an optional `age`, from 18 to 120;
  - `appearance`, up to 600 characters: what someone who meets the user sees;
  - `about`, up to 1,200 characters: what everyone who talks with this persona knows, such as personality, job and interests;
  - `is_default`, true for exactly one persona;
  - `status`, `active` or `archived`.

  Persona text passes the content guard of [ADR 0037](0037-content-guard-rules-for-character-text.md). The adult rule applies to the age as it does to a card.
- **The form warns about knowledge.** **Le tue persone** says: "Ciò che scrivi qui lo sa ogni personaggio che parla con questa persona." Private facts belong in the conversation, where they become memories with a source.
- **One persona per conversation, fixed.**
  - A conversation stores `persona_id` and `persona_note`, up to 600 characters: what this character knows of the user at the start, such as "Siete colleghi da due anni". The new-chat picker preselects the default persona, or the scenario's persona when the scenario names one (`scenarios.persona_id`, optional).
  - The persona cannot change later, because the conversation's memories about `user.*` describe that persona. The note can be edited at any time.
  - Persona edits apply from the next reply, as card edits do.
- **The prompt reads the conversation's persona.**
  - Every place that used `user.display_name` or `user.grammatical_gender` uses the persona's `name` and `grammatical_gender`.
  - A new block 10 sits in the stable text after block 3. It holds the age, `appearance`, `about` and the conversation's `persona_note`. Its budget is 0 tokens minimum, 150 as the target and 300 at most ([Prompting](../prompting.md#blocks-10-to-12)).
  - Under budget pressure, `appearance` is cut first, then `about`. The name and gender are in block 1 and are never cut.
  - Block 10 changes only when the persona or the note is edited, so the stable prefix holds between replies ([ADR 0031](0031-stable-prefix-dynamic-context-and-anchor.md)).
- **Shared memory follows the persona.**
  - **Condividi** stores the origin conversation's `persona_id` on the shared memory. A conversation reads only the shared memories of its own persona.
  - The migration sets the default persona on every existing shared memory.
- **Migration.**
  - P11's revision creates the default persona from the two settings, or from their defaults when no row exists. It sets the persona on every conversation and every shared memory, and removes the two keys.
  - The settings API no longer lists them. `GET` and `PUT /personas` take their place, and **Impostazioni generali** links to **Le tue persone**.
- **Deletion.** A persona that conversations use can only be archived. An archived persona keeps its conversations working and leaves the new-chat picker. A persona that no conversation uses can be deleted. The default persona cannot be archived or deleted until another one is the default.
- **Prepared, not delivered.** `appearance` could later describe the user in images where they appear. Version 1.1 never sends persona text to the image pipeline.

## Alternatives
- **Keep the persona swappable inside a conversation.** The memories about the user would then describe several people at once.
- **Put everything about the user in the persona.** Every character would then know every private detail from the first message. The note and the memories carry what only some characters know.
- **Snapshot the persona into each conversation, as scenarios are.** Editing one's own name or appearance would then need an edit in every chat. A scenario is a story's starting point; a persona is the user.
- **A per-character relationship table.** The note per conversation is simpler and matches how conversations are already isolated. A character with two conversations can have two different starting relationships.

## Consequences
- The user can keep several identities, and what a character knows about the user stays tied to one conversation, or to the shared memories of the same persona.
- Group conversations, if they come later, can reuse this: a group has one persona and one note per member.
- The stable text gains up to 300 tokens. At the catalog profiles' 12,288-token context, the targets of all blocks still leave about 7,600 tokens for messages ([Context composition](../memory-engine.md#planned-for-version-11)).
- Tests T-PSN-01 to T-PSN-06 ([Testing](../testing.md#personas)) verify this decision, and P11 in the [roadmap](../roadmap.md#p11-user-personas) delivers it. Until P11 runs, none of this is built.
