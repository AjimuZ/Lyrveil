# ADR 0002: Apache License 2.0 for original work

Status: accepted (2026-11-01). Supersedes: none.

## Context
The license must be compatible with the dependencies and with what is really distributed. KoboldCpp is AGPL-3.0 and ComfyUI is GPL-3.0. The application's direct dependencies are permissive. Lyrveil talks to both runtimes over HTTP, as separate processes, and neither its repository nor any published image contains them.

## Decision
Lyrveil's original code and documentation use Apache-2.0. The repository holds Dockerfiles that fetch the runtimes on the user's machine. The project publishes no prebuilt runtime images.

## Alternatives
- MIT: equally simple, but without the explicit patent grant.
- AGPL-3.0 or GPL-3.0: legally fine, and needless here. It would limit reuse of code that links none of the copyleft work.
- No license: the code would not really be open source.

## Consequences
Contributors grant Apache-2.0 rights. Anyone who publishes prebuilt runtime images takes on AGPL and GPL duties, as [Licensing](../licensing.md) explains. Model licenses are separate, and bind users regardless of this choice. This is engineering judgment, not legal advice.
