# Architecture decisions

One record per material decision. Records are never rewritten. A new record supersedes an old one.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-documentation-language-and-status.md) | English documentation, Italian product, explicit delivery status | Accepted |
| [0002](0002-license-apache-2.md) | Apache License 2.0 for original work | Accepted |
| [0003](0003-service-topology-and-networks.md) | Five services and segmented internal networks | Accepted |
| [0004](0004-single-sqlite-writer.md) | Backend is the only SQLite writer | Accepted |
| [0005](0005-exclusive-gpu-by-container-lifecycle.md) | GPU exclusivity through container stop and start | Accepted |
| [0006](0006-docker-socket-confined-to-orchestrator.md) | Docker socket only in the orchestrator, behind an allowlist | Accepted |
| [0007](0007-own-runtime-images-pinned.md) | Locally built runtime images from pinned versions | Superseded by [0028](0028-koboldcpp-pin-v1-124-2.md) |
| [0008](0008-persistence-on-wsl2-ext4-bind-mounts.md) | Bind mounts on the WSL2 Linux filesystem | Superseded by [0029](0029-windows-operation-with-docker-volumes.md) |
| [0009](0009-synchronous-sqlalchemy.md) | Synchronous SQLAlchemy behind a thread boundary | Accepted |
| [0010](0010-job-queue-and-scheduling-policy.md) | Durable SQLite job queue with mode batching | Accepted |
| [0011](0011-memory-retrieval-fts5-without-embeddings.md) | FTS5 with Snowball stemming, no embeddings | Accepted |
| [0012](0012-memory-provenance-and-history.md) | Memories with provenance, status and history | Accepted |
| [0013](0013-structured-output-grammar-and-validation.md) | Structured output through grammar, schema and bounded repair | Accepted |
| [0014](0014-chatml-plain-text-template.md) | ChatML as plain text over the native generate API | Superseded by [0025](0025-prompt-templates-per-model-family.md) |
| [0015](0015-angular-22-zoneless-signal-forms.md) | Angular 22, zoneless, Signal Forms, Angular Aria | Accepted |
| [0016](0016-realtime-websocket-channel.md) | One event WebSocket, with REST as the source of truth | Accepted |
| [0017](0017-local-only-security-model.md) | Local-only security model without authentication | Accepted |
| [0018](0018-adult-only-characters-and-content-safeguards.md) | Adult-only characters and deterministic content safeguards | Accepted |
| [0019](0019-hardware-tiers.md) | Target and reduced hardware tiers | Superseded by [0027](0027-hardware-tiers-after-ram-upgrade.md) |
| [0020](0020-proactive-messages-default-off.md) | Proactive messages off by default, missed events skipped | Accepted |
| [0021](0021-character-format-and-card-v2.md) | Own versioned JSON format, Character Card V2 after 1.0 | Accepted |
| [0022](0022-root-scripts-and-repository-layout.md) | Thin root scripts, implementations in `scripts/` | Superseded by [0029](0029-windows-operation-with-docker-volumes.md) |
| [0023](0023-obsidian-bloom-accessibility-extensions.md) | Control border and semantic tokens added to the palette | Accepted |
| [0024](0024-selectable-model-profiles.md) | Selectable model profiles with tier defaults | Accepted |
| [0025](0025-prompt-templates-per-model-family.md) | Prompt templates per model family over the native generate API | Accepted |
| [0026](0026-two-pass-image-rendering.md) | Two-pass image rendering with core ComfyUI nodes | Accepted |
| [0027](0027-hardware-tiers-after-ram-upgrade.md) | Hardware tiers after the reference machine's RAM upgrade | Accepted |
| [0028](0028-koboldcpp-pin-v1-124-2.md) | Locally built runtime images, with KoboldCpp pinned to v1.124.2 | Accepted |
| [0029](0029-windows-operation-with-docker-volumes.md) | Operation from Windows, with live data and models in Docker volumes | Accepted |
| [0030](0030-koboldcpp-efficiency-levers.md) | KoboldCpp efficiency levers measured in P02 | Accepted |
| [0031](0031-stable-prefix-dynamic-context-and-anchor.md) | Stable prompt prefix, dynamic context and a per-reply anchor | Accepted |
| [0032](0032-model-independent-reply-normalization.md) | Reply normalization, length bands and identity checks independent of the model | Accepted |
| [0033](0033-card-fields-for-a-model-independent-voice.md) | Card fields for a model-independent voice | Accepted |
| [0034](0034-character-consistency-protocol-and-verified-profiles.md) | Consistency protocol and verified profiles | Accepted |
| [0035](0035-structured-visual-identity-and-wardrobe.md) | Structured visual identity, wardrobe and tag vocabulary | Accepted |
| [0036](0036-structured-image-scenes-and-measured-levers.md) | Structured image scenes, sizes, captions and measured image levers | Accepted |
| [0037](0037-content-guard-rules-for-character-text.md) | Content guard rules for character text, with the character's own past allowed | Accepted |
| [0038](0038-vram-hold-in-the-orchestrator-wait-in-the-backend.md) | The orchestrator holds a load back for VRAM, the backend ends the job | Accepted |
| [0039](0039-scheduling-clocks-batches-and-stops.md) | Scheduling clocks, image batches, preemption and cancellation in the worker | Accepted |
