# ADR 0023: Control border and semantic tokens added to the palette

Status: accepted (2026-11-01). Supersedes: none.

## Context
The brief fixes nine color tokens and requires accessibility. Contrast computed on 2026-11-01 shows two gaps:

- The brand border `#493047` has 1.65, 1.47 and 1.33:1 against background, surface and elevated. Control boundaries need 3:1.
- The palette has no distinct error, warning or success color, and a destructive action styled in the brand pink reads as a normal action.

## Decision
- The nine brand tokens stay exactly as given.
- Decorative borders use the brand border. Control borders use `--ob-control-border`, equal to the secondary accent (4.66, 4.15 and 3.76:1).
- Three semantic tokens are added: `--ob-danger #E5737F`, `--ob-warning #E0B070`, `--ob-success #7FBF9A`, each at least 5.25:1 on the three main surfaces. Status always also has words or icons.
- Other shades derive from tokens with `color-mix`. A check refuses other color literals.
- Pixel fonts stay in titles and labels of at least 14 px, within the brief's permission for small labels, to keep accented letters readable.

## Alternatives
- Only the nine colors: fails WCAG for controls and makes destructive actions unclear.
- Darkening or changing the brand tokens: contradicts the brief.
- Default red, amber and green: they would clash with the muted palette.

## Consequences
The interface meets contrast rules, and the identity stays intact. The owner can change the three added colors without touching the brand tokens, because they are isolated in the token file. The ratios are recorded in [Design system](../design-system.md#contrast) and rechecked in P08.
