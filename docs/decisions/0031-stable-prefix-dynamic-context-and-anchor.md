# ADR 0031: Stable prompt prefix, dynamic context and a per-reply anchor

Status: accepted (2026-11-06). Supersedes: none. Refines the logical turns of [ADR 0025](0025-prompt-templates-per-model-family.md).

## Context
- `PromptFormatter` receives one system text built from blocks 1 to 6, then the turns ([Memory engine](../memory-engine.md#context-composition)). Blocks 4 and 5 hold state, time and memories, and they change on most turns, because retrieval follows the latest message. KoboldCpp reuses the cached prompt only up to the first changed token. Almost every reply therefore processes the whole history again: about 4,600 tokens at 8192, and up to about 12,000 at 16384.
- Identity and style appear only at the start of the prompt. In a long chat, thousands of tokens of history separate them from the reply. The catalog's profiles differ in how well they keep a persona over that distance, and the character must stay the same with each of them.
- KoboldCpp facts, read on 2026-11-06 in `koboldcpp.py` and `kcpp_src/gpttype_adapter.cpp` at commit `6d2fedf8624e5f05926af3191f9efdcb13c4133c`:
  - SWA (sliding-window attention) turns on by itself for models that support it, such as Gemma 4, and context shifting is then off. `--noswa` keeps a full-size cache instead. For Gemma 4 26B-A4B at 16384 tokens, that costs about 2.8 GiB more VRAM.
  - The reuse point can fall earlier than the positions the SWA cache still holds. The runtime then replays twice the window of cached tokens before the reuse point (2,048 for Gemma 4) instead of the whole prompt. The source calls this recovery approximate, and the startup message warns that it can degrade recall.
  - `--swapadding`, default 0, enlarges the SWA cache. The help text says that this "affects the rewind limit before reprocessing is forced".
  - `--smartcache [slots]` saves KV snapshots in RAM. When the cached prompt matches a new prompt by less than 70 %, it saves the cached state and restores a slot that matches by more than 70 %. A value of 2 or more sets the slot count, and no value or 1 gives 5 slots. It needs fast forwarding.

## Decision
- The logical prompt has four parts, in this order:
  1. **Stable system text**: blocks 1, 2, 3 and 6. It changes only with a card edit, a profile change or a new summary checkpoint.
  2. **History**: block 7, as plain turns. Old turns never carry dynamic text.
  3. **Dynamic context**: block 4, block 5 and the new block 9, the anchor, inside a fixed frame.
  4. **Current message**: block 8.
- Rendering per template family:
  - `gemma4` and `chatml`: the stable text is the system turn. The dynamic context opens the last user turn.
  - `mistral_v3_tekken`: the stable text, then the dynamic context, join the last user turn. The template in its GGUF puts system text there.
- The frame uses Italian markers with the character's first name and the user's display name:

  ```text
  [Contesto per {{first_name}}, non scritto da {{user}}]
  {block 4}
  {block 5}
  {block 9}
  [Messaggio di {{user}}]
  {block 8}
  ```

  Block 1 says that the bracketed context is not written by the user and is never quoted.
- **Anchor.** Code renders `anchor.v1.j2` from the card. It takes 60 to 160 tokens and is never reduced. It holds:
  - the first name and age;
  - the gender line;
  - the first sentence of up to two main traits;
  - the address form;
  - the length text of the character's band;
  - the narration and emoji rules;
  - up to two corrective notes ([ADR 0032](0032-model-independent-reply-normalization.md)).
- A line that starts with a frame marker can appear in user text, character text, memories or summaries. `PromptFormatter` replaces the opening bracket of such a line, so the marker no longer works. Validator V2 removes markers from replies.
- Structured tasks keep one system text and one user turn. A proactive message has no user message, so its last user turn holds the dynamic context and the proactive instruction.
- P04 measures three things with the [consistency protocol](0034-character-consistency-protocol-and-verified-profiles.md) scripts, on the target default and `rocinante-x-12b`:
  - **Layout.** `last_process` and the protocol results, for this layout and for the single system text of ADR 0025. This layout stays unless the single system text meets more hard targets. Changing back needs a new ADR.
  - **SWA padding** for Gemma 4 profiles: `SWA_PADDING` 0, 1024 and 2048. Keep the smallest value whose median `last_process` is within 10 % of the best, provided the VRAM margin rule and the speed rule still hold. Until then, 0 stays.
  - **SmartCache** on the target tier: `SMART_CACHE` 0 and 2. Use 2 for a profile if two conditions both hold:
    - the median `last_process` of the first chat reply after a structured task falls by at least 50 %;
    - the container's peak RAM stays at least 4 GiB below the memory Docker sees.

    The reduced tier keeps 0.

## Alternatives
- Keep blocks 4 and 5 in the system text. The layout stays simpler, but each reply reprocesses the history, and the persona stays far from the reply.
- Add a separate system turn at the end. Gemma 4's template has a single system turn, and Mistral v3 Tekken has none. A made-up turn falls outside what the models saw in training.
- Repeat the full identity at the end. That costs more tokens per reply, and it weighs the card more than the conversation. The anchor repeats only the details that most often drift.
- `--noswa`. Reuse would be exact at any distance, but it costs about 2.8 GiB more VRAM on the target default, which pushes expert weights into RAM.

## Consequences
- Old turns are rendered without context, so their tokens stay identical between replies.
  - A full-attention profile processes only the last exchange and the new context, about 1,000 to 2,500 tokens.
  - A Gemma 4 profile also replays up to 2,048 tokens, until SWA padding covers the distance.
- P02 changes `PromptFormatter`, the token budget and the golden tests (T-MOD-02), and adds the layout test T-MOD-05.
- The catalog and the launch file gain `swa_padding` and `smart_cache`, both 0 by default.
- Block 9 counts against the window. With 8192 tokens, about 4,500 remain for recent messages.
