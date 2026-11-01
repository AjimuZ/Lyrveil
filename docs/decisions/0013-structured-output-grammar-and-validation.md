# ADR 0013: Structured output through grammar, schema and bounded repair

Status: accepted (2026-11-01). Supersedes: none.

## Context
Extraction, character drafts, image prompts and proactive decisions need JSON. KoboldCpp's `GenerationInput` offers `grammar` for GBNF and has no `json_schema`, verified at `v1.124.1`. Model output is untrusted.

## Decision
1. Pydantic models are the single source of truth.
2. `schema_to_gbnf` converts a restricted subset of their JSON Schema to GBNF: objects with fixed keys, enums, bounded integers, strings, nullable values and arrays.
3. Generate through `/api/v1/generate` with low temperature.
4. Validate with Pydantic, then apply semantic rules in code.
5. On failure, try once more with the errors appended. Then fail with `STRUCTURED_OUTPUT_INVALID`, and leave durable state unchanged.

## Alternatives
- Validation with no grammar: more failures and retries.
- A generic JSON grammar only: valid JSON, but it allows wrong keys. This remains the fallback if a schema grammar performs badly (A-09).
- The OpenAI-compatible endpoint with JSON mode: indirect, and it brings the chat adapter problem of [ADR 0014](0014-chatml-plain-text-template.md).

## Consequences
Output is reliably parseable. The converter is a small piece of code to test. Grammar checks add some generation time, which T-GPU-05 measures. Semantic validity is never left to the grammar, so bad references or impossible values are still refused.
