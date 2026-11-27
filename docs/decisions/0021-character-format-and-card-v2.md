# ADR 0021: Own versioned JSON format, Character Card V2 after 1.0

Status: accepted (2026-11-01). Supersedes: none. Superseded in part by [ADR 0047](0047-character-card-import-and-export.md) (2026-11-27): Character Card V1, V2 and V3 are imported as JSON and PNG and exported too, in version 1.1. The `lyrveil.character` format stays.

## Context
The requirements ask for a documented, versioned JSON format, with no dependency on a proprietary format, and for Character Card V2 to be evaluated later if technically convenient. Lyrveil's card has separate traits, a communication profile and a visual profile, which V2's free-text fields do not model directly.

## Decision
- `lyrveil.character` version 1 is the exchange format, with a JSON Schema generated from the same Pydantic model as the API.
- Character Card V2 is deferred to after 1.0, as a one-way JSON import adapter, with no PNG-embedded cards at first. Mapped text goes into biography and traits, with visual fields empty, followed by the normal review in the Creator.
- Export to V2 is not planned, because it would lose structure.
- Age rules apply to any import.

## Alternatives
- V2 as the native format: it would flatten the card and limit memory and visual features.
- V2 import in 1.0: useful, but it takes time from the core, and imported cards often lack a stated age, which would need a mandatory review flow.
- Binary or archive format: harder to read and review. JSON suffices, with optional base64 assets.

## Consequences
The format can evolve with `format_version`. Lyrveil cannot read community cards in 1.0, and the roadmap lists this after 1.0. The app never requires any exchange format, since characters can be entered by hand.
