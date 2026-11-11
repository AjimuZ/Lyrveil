# ADR 0044: Accept certifi (MPL-2.0), pillow (MIT-CMU) and tslib (0BSD) as production dependencies

Status: accepted (2026-11-11, approved by the owner). Supersedes: none. Refines the production table of
[Licensing](../licensing.md#policy).

## Context
- The notices generator (`scripts/notices/generate.mjs`) refuses any production license outside the policy table.
  On the first run it reported three: `certifi` (MPL-2.0, backend and orchestrator), `pillow` (MIT-CMU, backend) and
  `tslib` (0BSD, frontend).
- Lyrveil is published under Apache-2.0 ([ADR 0002](0002-license-apache-2.md)).

## Decision
- `pillow` under MIT-CMU and `tslib` under 0BSD are accepted: both are permissive and belong with MIT and BSD.
  0BSD asks for nothing; MIT-CMU asks to keep the copyright and permission notice, which the notices file and the
  installed packages do.
- `certifi` under MPL-2.0 is accepted because Lyrveil uses it unmodified. MPL-2.0 copyleft is per file: it only
  requires keeping the notices of its files and making the source of those files available, which a link to the
  package satisfies. It does not reach Lyrveil's own files.
- The acceptance is recorded as exact `package` + `license` pairs in `scripts/notices/policy.json`. Any other package,
  or the same package under another license string, still fails the policy check.

## Alternatives
- **Replace certifi, pillow or tslib.** certifi is the trust store of the HTTP clients; pillow does the image work;
  tslib is required by the Angular compiler output. Replacing them costs more than the risk removed.
- **Add MPL-2.0, MIT-CMU and 0BSD to the general table.** It would also admit future packages without review.

## Consequences
- A new version that changes a license string fails the check until it is reviewed again.
- If Lyrveil ever patches certifi's files, those files must be published under MPL-2.0 and this ADR revisited.
