# Glossary

| Term | Meaning |
| --- | --- |
| Assisted creation | A character sheet drafted by the text model from a natural-language description, then edited by the user. |
| Autofit | KoboldCpp's `--gpulayers -1`: it chooses how many layers fit in free VRAM. |
| Canonical data | The character definition. Only an explicit user action changes it. |
| Checkpoint (summary) | One versioned summary of a message range. |
| Checkpoint (image) | The SDXL model file of the active image profile, loaded by ComfyUI. |
| ComfyUI | The image runtime. Lyrveil sends it fixed workflows over HTTP. |
| Consistency Engine | Rules that keep a character coherent: state updates, reply validators, canonical protection. |
| ContainerGate | The orchestrator module and only code that talks to the Docker socket. |
| Content guard | Deterministic checks that refuse content involving minors and enforce the adult-only rule. |
| ContextBuilder | Assembles the prompt for one reply within the token budget. |
| Desired mode | The runtime the backend wants on the GPU: `llm`, `image` or `none`. |
| DRY | A sampler that penalizes repeated token sequences. |
| Episode | A significant event in a conversation, with sources and links. |
| Foreground, background job | Foreground work has a waiting user. Background work runs only when idle and never changes the GPU mode. |
| GBNF | The grammar format KoboldCpp uses to constrain output. |
| Identity candidate | Something that might change canonical data. It is stored and shown for review, and never applied automatically. |
| KoboldCpp | The text runtime. It loads the active text profile's GGUF file and offers an HTTP API. |
| Launch level | `A`, `B` or `C`: context and batch values derived from the profile, lowered after out-of-memory failures. |
| Lease | Short-lived permission to use the current runtime. It works as the global GPU lock. |
| Memory Engine | Storage, extraction, retrieval and summaries of what a character remembers. |
| Memory Studio | The screen that shows and corrects what a character remembers. |
| Mixture of experts | A model that runs only some of its weights per token. Lyrveil's 26B profiles keep part of the experts in system RAM. |
| Mode | Which runtime is on the GPU. |
| Model profile | A catalog entry for one model file: source, pinned revision, size, hash, license, template and defaults. |
| Model Lifecycle Manager | The orchestrator's state machine that moves the GPU between modes. |
| NVML | NVIDIA's management library. Used here to read device memory. |
| Obsidian Bloom | Lyrveil's design system. |
| Preflight | Checks of Docker, GPU, memory, disk, directories and models before starting. |
| Quality preset | **Alta**, two image passes, or **Rapida**, one pass. |
| Proactive message | A message the character sends without a user message. Off by default. |
| Provenance | Where a memory comes from: `stated`, `inferred`, `fictional` or `user_confirmed`. |
| Scenario | A reusable starting situation for a conversation. |
| Scope | Whether a memory belongs to one conversation or is shared by a character. |
| Stage | A durable step of a job. A restart continues from the last finished one. |
| Tombstone | Hashes left by a deleted memory that block recreating it from the same source. |
| Variant | One version of a message: an edit, a regeneration or an alternative. |
| Volume | A Docker named volume. Lyrveil keeps live data in `lyrveil-data` and model files in `lyrveil-models`. |
| Window | The range of messages one extraction analyzes. |
| Working memory | The context built for one reply. It is rebuilt each time. |
