# ADR 0014: ChatML as plain text over the native generate API

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements ask for ChatML, to be verified with the model. Upstream facts read on 2026-11-01:

- The original model card lists ChatML for roleplay, Alpaca for story and Mistral.
- The GGUF metadata has no chat template.
- `tokenizer_config.json` has 1000 added tokens, none of them `<|im_start|>` or `<|im_end|>`. BOS is `<s>`, EOS is `</s>`.

So ChatML is supported by the author's guidance, but its markers are ordinary text. KoboldCpp's chat endpoint would have to guess a template.

## Decision
- Lyrveil builds the ChatML prompt itself and calls `/api/extra/generate/stream` and `/api/v1/generate`.
- Stop sequences at text level: `<|im_end|>`, `<|im_start|>`, `</s>`, and a line starting with the user's name.
- A validator removes any remaining marker.
- P02 records a real tokenization as a fixture.

## Alternatives
- `/v1/chat/completions` with `--chatcompletionsadapter AutoGuess`: less control, and the guess can change between versions.
- Mistral `[INST]` format: these tokens are special in this tokenizer, and it remains the documented fallback, but the author recommends ChatML for roleplay and the requirements ask for it.

## Consequences
The prompt is fully under control and is counted precisely. Stopping relies on text matching, so a marker could split across tokens, and KoboldCpp's stop handling and the validator cover that. If tests show unclean replies, a new template version moves to the Mistral format (A-02).
