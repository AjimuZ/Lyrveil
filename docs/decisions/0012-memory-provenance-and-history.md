# ADR 0012: Memories with provenance, status and history

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements separate stated facts, inferences, fiction, temporary information, contradicted information and user-confirmed memories. They forbid silent loss of history, require keeping uncertainty, and require that deleted memories do not immediately return. The model can be wrong and must not decide what counts as true.

## Decision
- Three independent fields: `provenance`, `durability` and `status`.
- Each change writes `memory_revisions`. Each memory links to source variants in `memory_sources`.
- The model proposes a relation. Deterministic rules decide: inferences are capped and cannot supersede facts, stated facts need a user source, and single-valued subjects cannot have two active values.
- Unresolved conflicts become `uncertain` in a conflict group, and the prompt labels them.
- User deletion removes the text and leaves a tombstone of hashes.
- State is an append-only log, and its current value is a replay.

## Alternatives
- One overwritten fact table: simple, but it loses history and cannot answer questions about the past.
- Full event sourcing for all memory: more complex than the problem needs.
- Letting the model resolve conflicts: not reliable, and not auditable.

## Consequences
The Memory Studio can show where each memory came from and how it changed. Edits to source messages can be handled precisely. There is more schema and more rule code. Deleted memories keep only hashes, so a deletion really removes the text, and backups are the one remaining copy until the user removes them.
