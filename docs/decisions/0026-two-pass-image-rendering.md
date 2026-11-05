# ADR 0026: Two-pass image rendering with core ComfyUI nodes

Status: accepted (2026-11-05). Supersedes: none.

## Context
On 2026-11-05 the owner asked for the best images this hardware can produce. The 1.0 design rendered one pass at about one megapixel. Facts:

- The Illustrious XL v1.0 card states native 1536 x 1536 and sizes such as 1248 x 1824 without changes. NoobAI-XL recommends a total area of about 1024 x 1024 for generation. A first pass near one megapixel keeps composition stable. A second pass at a larger size adds detail, especially to faces and hands.
- ComfyUI v0.43.0 offers everything needed as core nodes: `VAEDecode`, `ImageScaleBy` with `lanczos`, `VAEEncode`, `KSampler` with `denoise`, and the tiled VAE nodes.
- Face and hand detailers need detection models and custom nodes. Custom nodes are disabled by the security model, and the requirements exclude additional vision models.
- Upscaler networks such as ESRGAN are additional models. A pixel upscale followed by a partial denoise reaches most of the benefit without one.

## Decision
- Two quality presets. **Alta** is the default: a first pass at the SDXL bucket size, then a decode, a lanczos upscale by 1.5, an encode and a second `KSampler` pass with the same seed and `denoise` 0.45. Final sizes are 1248 x 1824, 1824 x 1248 and 1536 x 1536. **Rapida** is the single pass.
- The second pass uses `dpmpp_2m` with `karras` and 14 steps, because a non-ancestral sampler keeps the first pass's composition. The first pass keeps the profile's sampler.
- Only core nodes are used. `--disable-all-custom-nodes` stays.
- Out of memory retries the same preset with tiled decode and tiled encode, then falls back to **Rapida** once. The message says which preset produced the image.
- P05 measures time and VRAM and runs a grid. **Alta** stays the default if the 95th percentile of the render stage is at most 45 s on the reference machine. Otherwise **Rapida** becomes the default and **Alta** stays selectable.

## Alternatives
- Latent upscale: no extra decode and encode, but it needs a higher denoise and tends to add artifacts.
- ESRGAN or a similar upscaler before the second pass: slightly sharper input, but one more model with its own license and download.
- Face detailer: the largest gain for small faces, but it needs a detector and custom nodes. Candidate after 1.0 with its own ADR.
- A single pass directly at 1248 x 1824: Illustrious handles it, but composition at that size is less reliable than a first pass near one megapixel.

## Consequences
An **Alta** image takes roughly two to three times as long as **Rapida**, and P05 measures it. Files are about twice as large. Thumbnails do not change. Faces in wide scenes stay small, which is a documented limit. Progress shows the two passes separately.
