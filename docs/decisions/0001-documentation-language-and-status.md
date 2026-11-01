# ADR 0001: English documentation, Italian product, explicit delivery status

Status: accepted (2026-11-01). Supersedes: none.

## Context
The product's default language is Italian. The repository is public and needs a wider audience of contributors. The owner asked for complete documentation first, with no development yet and no open points, and with decisions taken rather than deferred.

## Decision
- Documents, identifiers and comments are in English. Product text, prompt templates and interface text are in Italian, and appear in the documents as such. The README has a short Italian summary.
- Every design document states its status. "Planned" means not built. Measured facts carry a date.
- Decisions that need a measurement are written as a default plus a fixed rule for the result.

## Alternatives
- Italian documentation: closer to the product, but it narrows the contributors, and it conflicts with the owner's engineering conventions.
- Bilingual documentation: double maintenance and guaranteed drift.
- Listing open questions for later: rejected by the owner's request.

## Consequences
The documents can serve directly as an implementation specification. Readers must not mistake them for delivered behavior, so the README and index state the status at the top. When a phase is delivered, its documents change in the same commit.
