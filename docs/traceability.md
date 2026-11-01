# Traceability

Maps each [requirement](requirements.md) group to its design, phase and tests. All implementation is planned. See the [roadmap](roadmap.md) for status.

| Requirements | Topic | Design | Phase | Tests |
| --- | --- | --- | --- | --- |
| R-PRD-01 to R-PRD-06 | Product | [Architecture](architecture.md), [Frontend](frontend.md) | P01 to P09 | AC-01 to AC-20 |
| R-HW-01 to R-HW-05 | Hardware | [Operations](operations.md#requirements), [ADR 0019](decisions/0019-hardware-tiers.md), [ADR 0008](decisions/0008-persistence-on-wsl2-ext4-bind-mounts.md) | P01 | T-DCK-07, T-DCK-08, T-GPU-12 |
| R-STK-01 | Frontend stack | [Frontend](frontend.md), [Technology](technology.md#frontend), [ADR 0015](decisions/0015-angular-22-zoneless-signal-forms.md) | P01 | Frontend unit tests, T-QLT-04 |
| R-STK-02 | Backend stack | [Backend](backend.md), [Technology](technology.md#backend-and-orchestrator), [ADR 0009](decisions/0009-synchronous-sqlalchemy.md) | P01 | Backend unit and integration tests |
| R-STK-03 to R-STK-05 | Infrastructure, runtimes | [Operations](operations.md#reference-compose-file), [ADR 0003](decisions/0003-service-topology-and-networks.md), [ADR 0007](decisions/0007-own-runtime-images-pinned.md) | P01, P02, P05 | T-DCK-01 to T-DCK-05 |
| R-MOD-01 to R-MOD-03 | Text model | [Runtime contracts](runtime-contracts.md), [Tuning](gpu-orchestration.md#measuring-and-tuning), [ADR 0014](decisions/0014-chatml-plain-text-template.md) | P02 | T-GPU-02 to T-GPU-04 |
| R-MOD-04, R-MOD-05 | Image model | [Image generation](image-generation.md#default-parameters) | P05 | T-GPU-06, T-QLT-02 |
| R-MOD-06, R-MOD-07 | Only two models, adapters | [Backend ports](backend.md#ports), [Risks](risks.md) | P02, P05 | Dependency review, T-DCK-10 |
| R-LNG-01 to R-LNG-05 | Italian | [Prompting](prompting.md), [Design text](design-system.md#interface-text), [Frontend](frontend.md#text-and-translation) | P02, P04, P08 | T-QLT-01 |
| R-LNG-06 | Italian image requests | [Image generation](image-generation.md#visual-promptbuilder) | P05 | T-IMG-01, T-IMG-03, T-GPU-06 |
| R-LNG-07 | Long conversation checks | [Quality protocol](prompting.md#italian-quality-protocol) | P04, P08 | T-QLT-01 |
| R-MSG-01 to R-MSG-03 | Chat list | [Frontend screen 1](frontend.md#1-conversation-list), [API](api.md#conversations) | P02, P07 | T-MSG-01, T-MSG-10, T-MSG-11 |
| R-MSG-04 to R-MSG-07 | Conversation | [Frontend screen 2](frontend.md#2-conversation), [API](api.md#messages), [Data model](data-model.md#messages) | P02, P05, P07 | T-MSG-02 to T-MSG-09, T-MSG-13 |
| R-MSG-08 | Images as messages | [Image persistence](image-generation.md#persistence) | P05 | T-IMG-06 to T-IMG-08 |
| R-MSG-09 to R-MSG-11 | Independent conversations | [Data model](data-model.md#conversations), [Memory](memory-engine.md#retrieval-contract), [Consistency](consistency-engine.md) | P03, P04 | T-MSG-09, T-CON-02, T-CON-03, T-MEM-15 |
| R-CHR-01 to R-CHR-06 | Card | [Character system](character-system.md#card-schema), [Frontend screen 3](frontend.md#3-character-creator) | P03 | T-CHR-01 to T-CHR-04, T-CHR-07, T-CHR-13 |
| R-CHR-07, R-CHR-08 | Assisted creation | [Character system](character-system.md#assisted-creation) | P03 | T-CHR-10, T-GPU-05 |
| R-CHR-09, R-CHR-10 | Avatars | [Character system](character-system.md#avatars-and-images) | P03, P05 | T-CHR-11, T-CHR-12 |
| R-CHR-11, R-CHR-12 | Exchange | [Format](character-system.md#exchange-format), [ADR 0021](decisions/0021-character-format-and-card-v2.md) | P03 | T-CHR-05, T-CHR-06 |
| R-MEM-01 to R-MEM-09 | Layers | [Memory layers](memory-engine.md#memory-layers), [ADR 0012](decisions/0012-memory-provenance-and-history.md) | P04 | T-MEM-15, T-MEM-18, T-CON-04 |
| R-MEM-10 to R-MEM-14 | Extraction | [Pipeline](memory-engine.md#extraction-pipeline), [ADR 0013](decisions/0013-structured-output-grammar-and-validation.md) | P04 | T-MEM-01 to T-MEM-04, T-MEM-14 |
| R-MEM-15 to R-MEM-17 | Retrieval | [Retrieval](memory-engine.md#retrieval), [ADR 0011](decisions/0011-memory-retrieval-fts5-without-embeddings.md) | P04 | T-MEM-05 to T-MEM-07, T-MEM-22 |
| R-MEM-18 to R-MEM-20 | Contradictions | [Contradictions](memory-engine.md#contradictions) | P04 | T-MEM-10 to T-MEM-13 |
| R-MEM-21 to R-MEM-23 | Summaries | [Summaries](memory-engine.md#progressive-summaries) | P04 | T-MEM-16, T-MEM-17 |
| R-MEM-24 to R-MEM-27 | Context | [Context composition](memory-engine.md#context-composition) | P04 | T-MEM-21 |
| R-CON-01 to R-CON-06 | Consistency rules | [Rules](consistency-engine.md#rules-and-enforcement) | P04, P05 | T-CON-01 to T-CON-08 |
| R-CON-07 to R-CON-09 | State manager | [State manager](consistency-engine.md#character-state-manager) | P04 | T-CON-03, T-CON-06, T-MEM-23 |
| R-CON-10 | Versioning | [Versioning](consistency-engine.md#versioning) | P03 | T-CHR-03, T-CHR-08, T-CHR-09 |
| R-STU-01 | Memory Studio | [Frontend screen 5](frontend.md#5-character-memory-studio), [API](api.md#memory) | P04 | T-MEM-08, T-MEM-11 |
| R-STU-02 | No recreation | [Tombstones](memory-engine.md#deletion-and-tombstones) | P04 | T-MEM-09 |
| R-STU-03 | Invalidation | [Invalidation](memory-engine.md#invalidation-after-message-changes) | P04 | T-MEM-19, T-MEM-20 |
| R-STU-04 | Diagnostics | [Data model](data-model.md#prompt_assemblies), [API](api.md#messages) | P04 | T-MEM-21 |
| R-IMG-01 to R-IMG-04 | Modes | [Modes](image-generation.md#modes) | P05 | T-IMG-03, T-IMG-07, T-IMG-13 |
| R-IMG-05 to R-IMG-08 | Prompt and consistency | [PromptBuilder](image-generation.md#visual-promptbuilder), [Consistency](image-generation.md#visual-consistency) | P05 | T-IMG-01, T-IMG-02, T-CON-07, T-QLT-02 |
| R-IMG-09, R-IMG-10 | Persistence | [Persistence](image-generation.md#persistence) | P05 | T-IMG-06, T-IMG-08 |
| R-GPU-01 to R-GPU-04 | Exclusive GPU | [State machine](gpu-orchestration.md#state-machine), [18 steps](gpu-orchestration.md#the-18-steps-of-an-image-request), [ADR 0005](decisions/0005-exclusive-gpu-by-container-lifecycle.md) | P01, P06 | T-ORC-01, T-ORC-02, T-IMG-04, T-GPU-07 |
| R-GPU-05 | Reliability | [Leases](gpu-orchestration.md#leases-and-the-global-lock), [Scheduling](gpu-orchestration.md#scheduling-rules), [Recovery](gpu-orchestration.md#recovery), [ADR 0010](decisions/0010-job-queue-and-scheduling-policy.md) | P06 | T-ORC-03 to T-ORC-12, T-GPU-10, T-GPU-11 |
| R-GPU-06, R-GPU-07 | Release, external programs | [Verification](gpu-orchestration.md#verifying-gpu-release) | P06 | T-ORC-04, T-ORC-05, T-GPU-09 |
| R-GPU-08 | No `down` | [Operations](operations.md#scripts), [ADR 0005](decisions/0005-exclusive-gpu-by-container-lifecycle.md) | P06 | T-GPU-07 |
| R-GPU-09, R-GPU-10 | Concurrency | [Scheduling](gpu-orchestration.md#selection) | P06 | T-ORC-12, T-ORC-13, T-MSG-09, T-GPU-08 |
| R-SCH-01 to R-SCH-04 | Proactive | [Scheduler](scheduler.md), [ADR 0020](decisions/0020-proactive-messages-default-off.md) | P07 | T-SCH-01, T-SCH-02, T-SCH-04, T-SCH-05 |
| R-SCH-05 | Time context | [Time context](scheduler.md#time-context) | P07 | T-SCH-07 |
| R-SCH-06, R-SCH-07 | Missed events | [Missed events](scheduler.md#missed-events) | P07 | T-SCH-03 |
| R-UI-01 to R-UI-04 | Style | [Design system](design-system.md), [ADR 0023](decisions/0023-obsidian-bloom-accessibility-extensions.md) | P08 | Color literal check |
| R-UI-05, R-UI-06 | Screens | [Screens](frontend.md#screens) | P02 to P08 | T-QLT-03 |
| R-UI-07, R-UI-08 | Responsive | [Layout](design-system.md#layout) | P08 | T-QLT-04 |
| R-UI-09 | Accessibility | [Accessibility](design-system.md#accessibility), [Keyboard](frontend.md#keyboard) | P08 | T-QLT-03 |
| R-BE-01 to R-BE-03 | Modules | [Services](backend.md#services), [Layers](architecture.md#layers) | P01 to P07 | Import-rule test |
| R-BE-04 to R-BE-06 | Schema | [Data model](data-model.md), [Deletion](data-model.md#deletion-policy), [Migrations](data-model.md#migrations) | P01 to P07 | Migration test, T-DCK-04 |
| R-BE-07 | Backup, export, restore | [Backup](operations.md#backup-and-restore) | P03 | T-DCK-12, T-CHR-05 |
| R-OPS-01, R-OPS-02 | Services | [Components](architecture.md#components), [Compose](operations.md#reference-compose-file) | P01 | T-DCK-05 |
| R-OPS-03, R-OPS-04 | Docker security | [Docker socket](security.md#docker-socket), [ADR 0006](decisions/0006-docker-socket-confined-to-orchestrator.md) | P01 | T-ORC-07, T-ORC-10, T-DCK-09 |
| R-OPS-05 to R-OPS-07 | Scripts | [Scripts](operations.md#scripts), [ADR 0022](decisions/0022-root-scripts-and-repository-layout.md) | P01 | T-DCK-01 to T-DCK-07 |
| R-OPS-08 | Model download | [Download](operations.md#scriptsdownload-modelssh) | P01 | T-DCK-11 |
| R-PUB-01 to R-PUB-03, R-PUB-09 | Repository | [Publishing](publishing.md#never-commit), `.gitignore`, `.dockerignore` | P00, P09 | [Documentation checks](verification/2026-11-01-documentation.md), audit |
| R-PUB-04 | Licenses | [Licensing](licensing.md), [ADR 0002](decisions/0002-license-apache-2.md) | P00, P09 | Audit |
| R-PUB-05, R-PUB-06 | README, demo data | [README](../README.md), [Demo characters](character-system.md#demo-characters) | P00, P08 | Audit |
| R-PUB-07, R-PUB-08 | Git process | [Publishing](publishing.md), [CONTRIBUTING](../CONTRIBUTING.md#commits) | All | Audit |
| R-TST-01 to R-TST-08 | Tests | [Testing](testing.md) | All | The catalog itself |
| R-ENG-01 to R-ENG-04 | Delivery | [Roadmap](roadmap.md), [CONTRIBUTING](../CONTRIBUTING.md), [ADRs](decisions/README.md) | All | Review |
| R-SAF-01 to R-SAF-03 | Content safeguards | [Security](security.md#content-safeguards), [ADR 0018](decisions/0018-adult-only-characters-and-content-safeguards.md) | P03, P05 | T-CHR-02, T-SEC-07, T-IMG-01 |
| R-SAF-04, R-SAF-06 | Simulation, wellbeing | [Prompting](prompting.md#block-1-global-instructions), [Interface text](design-system.md#interface-text) | P02, P08 | T-QLT-01 |
| R-SAF-05 | No pressure | [Scheduler safety](scheduler.md#safety) | P07 | T-SCH-05, T-QLT-01 |

## Acceptance criteria

| Criterion | Verified by |
| --- | --- |
| AC-01 to AC-04 | T-DCK-01, T-DCK-11, clean-clone run |
| AC-05, AC-06 | T-CHR-01, T-CHR-13 |
| AC-07 | T-CHR-11, T-CHR-12 |
| AC-08, AC-09 | T-MSG-01 to T-MSG-03, T-QLT-01 |
| AC-10 | T-MSG-09, T-CON-01 |
| AC-11 | T-MEM-18, T-CON-02 |
| AC-12 | T-MEM-08 to T-MEM-13 |
| AC-13, AC-14 | T-IMG-07, T-GPU-06 |
| AC-15 | T-IMG-04, T-GPU-07 |
| AC-16 | T-DCK-04, T-CHR-07, T-MEM-18 |
| AC-17 | T-SCH-01 to T-SCH-03 |
| AC-18 | T-DCK-08, T-GPU-12 |
| AC-19 | T-ORC-02, T-GPU-07 |
| AC-20 | Pre-publication audit, T-DCK-10 |
