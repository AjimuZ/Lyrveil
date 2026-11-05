# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

No application exists yet. This section records the design documentation.

### Added

- Product requirements with stable identifiers and acceptance criteria.
- Architecture for the five Docker services, the data model, the REST, WebSocket
  and control APIs, and the verified contracts of the two runtimes.
- Design of the Lyrveil Memory Engine, the Character Consistency Engine, the Model
  Lifecycle Manager, image generation, proactive messages and the Character Creator.
- The Obsidian Bloom design system and the specification of the ten screens.
- Operations, security, licensing, testing and publication guides, a phased
  roadmap with exit checks, a risk register and 28 architecture decision records.
- Repository hygiene files that keep model weights, user data and secrets out of
  Git and Docker images.
- A model catalog: the user chooses the active text and image models from verified
  profiles, and can register local files as unverified custom profiles.
- Two image quality presets: **Alta**, a two-pass render up to 1248 x 1824, and
  **Rapida**, a single pass.

### Changed

- The default text model is Gemma 4 26B-A4B on 32 GB machines and Gemma 4 12B on
  16 GB machines, instead of Rocinante 12B v1.1, which stays selectable.
- The default image model is Illustrious XL v2.0 instead of v1.0, which stays
  selectable. NoobAI-XL 1.1 is also offered.
- Prompts follow the official template of each model family.
- The 32 GB machine is the target tier, and KoboldCpp is pinned to `v1.124.2`
  because `v1.124.1` was withdrawn upstream.
