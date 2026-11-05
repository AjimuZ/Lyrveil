# ADR 0025: Prompt templates per model family over the native generate API

Status: accepted (2026-11-05). Supersedes: [ADR 0014](0014-chatml-plain-text-template.md).

## Context
[ADR 0014](0014-chatml-plain-text-template.md) wrote ChatML for one model. With selectable profiles ([ADR 0024](0024-selectable-model-profiles.md)) the catalog has three prompt formats. Facts read on 2026-11-05:

- **Gemma 4.** The official `chat_template.jinja` of `google/gemma-4-26B-A4B-it` and `google/gemma-4-12B-it`, rendered in a sandbox, gives `<|turn>system\n…<turn|>\n<|turn>user\n…<turn|>\n<|turn>model\n`. With thinking off, the generation prompt ends with an empty thought channel, `<|channel>thought\n<channel|>`. `<|turn>` (105), `<turn|>` (106), `<|channel>` (100) and `<channel|>` (101) are special tokens, and 106 is listed as an end token in `generation_config.json`. The GGUF templates of the official 12B file and of Orion 26B-A4B render the same text.
- **Mistral v3 Tekken.** The Rocinante-X card asks for "Mistral v3 Tekken (NOT v7)" without `[SYSTEM_PROMPT]`. The template in its GGUF renders `[INST]U1[/INST]A1</s>[INST]SYS\n\nU2[/INST]`: the system text joins the last user turn. `[INST]` and `[/INST]` are special tokens.
- **ChatML** for Rocinante 12B v1.1, as verified in ADR 0014: the markers are ordinary text in that tokenizer.
- **KoboldCpp v1.124.2.** `TokenizeString` calls `common_tokenize(ctx, text, add_bos, true)`. The last argument parses special tokens, so control tokens written as text in the prompt become the real tokens. BOS is added by the runtime.

## Decision
- Lyrveil renders the prompt itself and keeps `/api/extra/generate/stream` and `/api/v1/generate`, which offer `grammar`.
- `PromptFormatter` has one implementation per family: `gemma4`, `mistral_v3_tekken` and `chatml`. Each one renders the same logical turns: one system text, then user and character turns.
- Templates never write BOS, because KoboldCpp adds it.
- Thinking is off for every task. `gemma4` always ends the generation prompt with the empty thought channel, exactly as the official template does with thinking off.
- Each family declares its stop sequences. Text stops back up the end tokens, and the validator removes any leftover control text.
- P02 records one tokenization fixture per family with the real models in the catalog, and a golden string test compares each formatter with the official template output.

## Alternatives
- `/v1/chat/completions` with `--jinja`: the GGUF templates are complete for Gemma 4, but the chat endpoint gives less control over grammar, prompt prefix stability and token counting, and Rocinante 12B v1.1 has no template.
- A free-form template text per profile: flexible, but a user mistake would silently degrade every reply. Three verified families cover the catalog.
- Thinking on for structured tasks: better reasoning at the cost of hundreds of hidden tokens per call, and grammar would have to allow the thought channel. It is a measured option after 1.0.

## Consequences
A new model family means a new formatter, its tests and a fixture. Prompt rendering is pure and testable without a model. Validator V2 in the [consistency engine](../consistency-engine.md) checks control text of the active family instead of ChatML only.
