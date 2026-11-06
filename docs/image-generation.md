# Image generation

Status: accepted (2026-11-01), revised 2026-11-05 for image profiles and two-pass rendering, and 2026-11-06 for the
structured identity, scenes and captions. Implementation: planned, phase P05.

Images are normal messages. The text model writes a structured scene and a caption, code builds the prompt, ComfyUI
renders it, and the backend stores the result independently of the runtime. The GPU hand-over is described in
[GPU orchestration](gpu-orchestration.md).

## Modes

### Manual mode

The user asks for an image in two ways:

1. **Explicit action.** The composer's image button opens a small form. It holds the request, the aspect (**Automatico** by
   default), the outfit (**Abituale**, a named outfit or **Della scena**), the quality (**Alta** or **Rapida**) and an
   optional seed, steps and CFG. It calls `POST /conversations/{id}/images`.
2. **Message text.** With `intent: auto`, a deterministic detector reads the message. It needs an imperative request verb and an
   image noun within 6 words. Lists are stored per language.

| Language | Verbs | Nouns |
| --- | --- | --- |
| Italian | `mandami`, `inviami`, `fammi vedere`, `mostrami`, `disegna`, `disegnami`, `genera`, `generami`, `crea`, `creami`, `fammi`, `scatta` | `foto`, `fotografia`, `immagine`, `disegno`, `ritratto`, `selfie`, `illustrazione`, `schizzo` |

The detector ignores negated requests such as `non mandarmi`, and statements such as `ti ho mandato una foto`. When it matches, the
message is saved and an `image_request` job replaces the `chat_reply` job. The pending bubble has a cancel action, so a false
positive costs one click. `images.auto_detect_requests` turns the detector off. A test file holds at least 50 positive and 50
negative phrases.

### Contextual mode

Disabled by default with `images.contextual_enabled = false`. No image is ever generated automatically after a message.

When enabled:

1. After a reply, at most once every `images.proposal_min_messages` messages (default 20) and at most once per hour, a background
   `image_proposal` job runs in `LLM_READY`.
2. The model returns a structured decision:

   ```python
   class ImageProposalResult(BaseModel):
       schema_version: Literal[1]
       propose: bool
       scene: Annotated[str, StringConstraints(max_length=300)]
       reason: Annotated[str, StringConstraints(max_length=200)]
   ```

3. The backend validates it and applies the content guard. Only a valid `propose = true` creates an `image_proposals` row and the
   `image.proposed` event.
4. The conversation shows a card: "Nerea vorrebbe mandarti un'immagine: *la mappa aperta sul tavolo del porto*. **Sì** / **No**".
5. Only **Sì** creates `image_request`. Unanswered proposals expire after 24 hours.

### Character mode

`POST /characters/{id}/assets/generate` covers avatars, profile images, set illustrations, contextual scenes and variants. It uses
the same job and pipeline, with `purpose` set accordingly. The result is linked as a `character_assets` row and appears in the gallery.

## Job stages

`image_request` is one durable job. `stage` records the last finished step, so a restart continues without repeating work.

| Stage | `required_mode` | Work | Saved at the end |
| --- | --- | --- | --- |
| `prompt` | `llm` | Writes the [caption](#caption), generates `ImageScene`, builds the final prompt, runs the content guard | `payload_json.final_prompt`, `payload_json.scene`, `payload_json.caption`, `seed`, size |
| `render` | `image` | Submits the workflow of the chosen preset, follows progress, fetches the file into staging | Staged file path, preset used |
| `persist` | `none` | Validates, writes the thumbnail, moves files, updates the message | `generated_images`, the variant |

The seed is chosen and saved in `prompt`, so a retried `render` reproduces the same request.

## Visual PromptBuilder

`VisualConsistencyService` joins a deterministic identity with the model's structured scene
([ADR 0035](decisions/0035-structured-visual-identity-and-wardrobe.md),
[ADR 0036](decisions/0036-structured-image-scenes-and-measured-levers.md)). The text model never writes identity tags,
weights or prompt syntax, so the identity part of the prompt is identical whichever text profile is active.

### Input to the model

The model receives the visual profile's Italian descriptions, the wardrobe labels, the setting, recent messages, the user's request
and the conversation language. It returns English values:

```python
class ImageScene(BaseModel):
    schema_version: Literal[1]
    framing: Literal["close-up", "portrait", "upper body", "cowboy shot", "full body", "wide shot"]
    angle: Literal["straight-on", "from side", "from behind", "from above", "from below", "pov", "dutch angle"] | None
    expression: Annotated[list[str], Field(max_length=3)]      # Vocabulary tags
    pose: Annotated[list[str], Field(max_length=4)]            # Vocabulary tags
    outfit: str                                                # "usual", a named outfit key, or "scene"
    scene_clothing: Annotated[list[str], Field(max_length=6)]  # Only when outfit is "scene"
    location: Annotated[list[str], Field(max_length=6)]
    time_of_day: Literal["day", "sunrise", "sunset", "twilight", "night"] | None
    lighting: Annotated[list[str], Field(max_length=3)]        # Vocabulary tags
    objects: Annotated[list[str], Field(max_length=4)]
    extra_negative: Annotated[list[str], Field(max_length=6)]
```

Rules:

- A grammar built for each request limits `outfit` to `usual`, the character's outfit keys and `scene`. When `clothing` is locked,
  `scene` is not offered. When the user picks an outfit in the form, the grammar allows only that one.
- `expression`, `pose` and `lighting` accept vocabulary tags only. The other lists accept free tags of at most 4 words.
- The template is `image_scene.v1.j2`, with one worked example.

### Caption

The caption becomes the text of the message when the image is ready. It is written in the `prompt` stage, before the scene,
because the chat prompt is still in the runtime's cache. It uses the normal reply path, with the stable text, the history,
the dynamic context and the [anchor](prompting.md#block-9-anchor). The dynamic context gets one extra line:

```jinja
Stai per mandare a {{ user.display_name }} un'immagine: {{ request }}. Scrivi solo il messaggio che la accompagna.
```

The reply checks and the [normalizer](consistency-engine.md#reply-normalization) apply. `max_length` is the band's value, at
most 120. If the caption fails, the image is sent without text. Both calls happen before the GPU changes hands.

### Final prompt

Code assembles the prompt in a fixed order:

```text
positive = quality tags, rating tag, subject tag, "adult", age band,
           identity: hair color, hair length, hairstyle, eye color, skin, face, body, marks,
           outfit tags, accessories,
           scene: expression, pose, framing, angle, location, time of day, lighting, objects,
           style tags, extra tags
negative = base negative, safety negative, persistent negative, extra negative after the sanitizer
```

Example for Nerea, with the usual outfit and the `illustrious` preset:

```text
masterpiece, best quality, amazing quality, absurdres, general, 1girl, adult, woman, red hair, short hair, messy hair,
green eyes, freckles, slim, ink stains, canvas jacket, many pockets, necklace, smile, sitting, upper body, harbor,
sunset, warm lighting, old map, table, soft colors, brass compass pendant
```

Quality tags, rating tag and base negative come from the image profile's preset:

| Part | `illustrious` preset: `illustrious-xl-v2.0`, `illustrious-xl-v1.0`, custom | `noobai` preset: `noobai-xl-1.1` |
| --- | --- | --- |
| Quality tags | `masterpiece, best quality, amazing quality, absurdres` | `masterpiece, best quality, newest, absurdres, highres` |
| Rating tag | `general` | `safe` |
| Base negative | `worst quality, bad quality, low quality, lowres, bad anatomy, bad hands, missing fingers, extra digits, jpeg artifacts, signature, watermark, text, username` | `worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, mutated hands, mammal, anthro, furry, ambiguous form, feral, semi-anthro` |

The NoobAI values come from its model card. `nsfw` is removed from the card's negative because the safety negative
always holds it. The card orders caption tags as subject count, character, series, artists, special tags, general tags,
then other tags. Lyrveil's order keeps the subject first and puts the character's identity where named characters would go.

| Part | Value for every profile |
| --- | --- |
| Safety negative | `child, loli, shota, toddler, underage, minor, nsfw, explicit, nude`. Always appended |

The age band always follows `adult`:

| Age | `1girl` | `1boy` | `1other` |
| --- | --- | --- | --- |
| 18 to 29 | `woman` | `man` | `person` |
| 30 to 44 | `mature female` | `mature male` | `mature` |
| 45 to 64 | `mature female, middle-aged` | `mature male, middle-aged` | `mature, middle-aged` |
| 65 and over | `old woman, wrinkles` | `old man, wrinkles` | `old, wrinkles` |

These are starting values. The [image identity protocol](#image-identity-protocol) checks them.

### Caps and prompt chunks

ComfyUI encodes the prompt in chunks of 75 tokens plus start and end tokens and concatenates them. SDXL's pooled vector
comes from the first chunk only ([Runtime contracts](runtime-contracts.md#text-encoding)). The order above puts the subject,
age band and identity first.

| Part | Cap |
| --- | --- |
| Identity | 16 tags |
| Outfit and accessories | 12 tags |
| Scene | 18 tags |
| Style | 4 tags |
| Extra | 6 tags |

The builder counts tokens with the vocabulary's counts, two per word for free tags, plus one per comma. It records the total
and whether identity ends inside the first chunk. The Creator warns when it would not.

### Tag vocabulary

`backend/src/lyrveil/images/vocabulary/tags.v1.json` is an original list, released under Apache 2.0 with the code. It holds no
text copied from tag wikis. Each entry has:

| Field | Use |
| --- | --- |
| `tag` | The English tag, as written in the prompt |
| `category` | `hair_color`, `hair_length`, `hairstyle`, `eye_color`, `skin`, `face`, `body`, `marks`, `accessories`, `style`, `expression`, `pose`, `lighting` |
| `aliases_it` | Italian words that map to the tag, used by the fallback scene and the Creator's search |
| `clip_tokens` | Token count, computed with ComfyUI's CLIP tokenizer when P05 builds the file |

The grammar of `visual_profile_translate` takes its closed values from this file. T-IMG-16 checks that every entry passes the
sanitizer and that its token count matches the tokenizer.

### Sanitizer

Every tag that comes from a model or a user passes the same function:

1. Lowercase, trim, replace `_` with a space.
2. Remove the word `embedding:` and anything joined to it.
3. Keep only letters, digits, spaces, hyphens, apostrophes and periods. Parentheses, colons, brackets, backslashes and
   commas inside a tag are removed, so no tag can change weights or split itself.
4. Collapse spaces. An empty result is dropped.

Vocabulary entries use only the characters the sanitizer keeps, so each one passes it unchanged and none needs an escape.
Only the builder writes weights. In the gallery's advanced dialog, user weights are clamped to 0.5 to 1.5, a literal
parenthesis is written `\(` or `\)`, and `embedding:` is refused with `PROMPT_SYNTAX_REFUSED`.

### Scene filter

Each scene tag is processed in order:

1. The sanitizer.
2. Reject tags with more than 4 words, unknown tags in vocabulary lists, duplicates, and tags already in the identity.
3. Reject blocked terms. A hit fails the job with `CONTENT_POLICY_BLOCKED`.
4. Remove tags in an identity category: hair, eyes, skin, face, body and marks. The vocabulary's categories and aliases decide.
5. When the `accessories` or `distinctive_marks` field is locked, remove scene tags of that category.

## Visual consistency

| Aspect | Mechanism |
| --- | --- |
| Hair, eyes, physical features, marks, accessories, style | `identity_en` slots, rendered by code in a fixed order for every image. They are byte-identical whatever the text model is. Scene tags cannot override them |
| Age | The age band after `adult` |
| Recurring clothing | The usual outfit or a named outfit, chosen by the scene or by the user. A scene outfit is possible unless `clothing` is locked |
| Profile changes | A changed description starts `visual_profile_translate`. It fills the unlocked slots with `VisualIdentityResult`, under a grammar built from the vocabulary |
| Seed | Policy `random`, `fixed` or `base_offset`. Stored for every image |

The English tags are generated once and stay editable, so each image does not need a new translation.

**Limit:** the seed alone does not preserve a character. Different prompts or sizes produce different images even with the same seed.
A fixed identity prompt makes the description identical, but it cannot guarantee the same face. That needs reference techniques,
which 1.0 prepares but does not activate.

### Image identity protocol

T-QLT-07 runs in P05 ([ADR 0036](decisions/0036-structured-image-scenes-and-measured-levers.md)):

- the 3 demo characters and the 2 synthetic test characters, aged 52 and 70;
- 4 scenes and 3 seeds, with the scenes written by every catalog text profile;
- the owner checks hair color and length, eye color, age band and usual outfit on a fixed sheet.

| Measure | Target |
| --- | --- |
| Attribute checks correct | At least 90 % |
| Gap between any text profile and the default | At most 5 points |

The result goes to `docs/verification/`. A failing age band is changed in the vocabulary and measured again.

## Default parameters

The active [image profile](models.md#image-profiles) selects the checkpoint and its preset
([ADR 0024](decisions/0024-selectable-model-profiles.md)). The quality preset selects one or two passes
([ADR 0026](decisions/0026-two-pass-image-rendering.md)).

### First pass

| Parameter | `illustrious` | `noobai` | Range | Source |
| --- | --- | --- | --- | --- |
| Square | 1024 x 1024 | Same | Fixed preset | Requirements |
| Portrait | 832 x 1216 | Same | Fixed preset | SDXL bucket, recommended by the NoobAI card |
| Landscape | 1216 x 832 | Same | Fixed preset | SDXL bucket |
| Tall | 768 x 1344 | Same | Fixed preset | Listed by the NoobAI card |
| Wide | 1344 x 768 | Same | Fixed preset | Listed by the NoobAI card |
| Batch size | 1 | Same | Fixed | Requirements |
| Seed | Random 32-bit, stored | Same | 0 to 4294967295 | Requirements |
| Steps | 28 | 28 | 10 to 50 | Community guidance. NoobAI card: 25 to 30 |
| CFG | 5.5 | 5.5 | 1 to 12 | Community guidance. NoobAI card: 5 to 6 |
| Sampler | `euler_ancestral` | `euler_ancestral` | Allowlist from ComfyUI's `KSAMPLER_NAMES` | Community guidance. NoobAI card: Euler a |
| Scheduler | `normal` | `normal` | `normal`, `karras` | Project default |
| CLIP skip | 2 | 2 | 1 or 2 | Community guidance. Not stated for NoobAI, so the grid tests both |

No OnomaAI card gives sampler, steps or CFG. The Illustrious defaults come from third-party guides, which agree on
Euler ancestral, 20 to 28 steps, CFG from 3 to 7 and CLIP skip 2. They are not official.

The aspect `auto`, the default for chat images, follows the scene's framing:

| `framing` | Aspect |
| --- | --- |
| `close-up`, `portrait`, `upper body`, `cowboy shot` | Portrait |
| `full body` | Tall |
| `wide shot` | Wide |

Avatars use square. The user can choose any aspect in the form.

### Quality presets

| Preset | Passes | Final size: square, portrait, landscape, tall, wide | Planning time on the reference card |
| --- | --- | --- | --- |
| **Alta**, default | First pass, then a second pass at 1.5 times the size | 1536 x 1536, 1248 x 1824, 1824 x 1248, 1152 x 2016, 2016 x 1152 | About 20 to 30 s of rendering, plus the checkpoint load |
| **Rapida** | First pass only | 1024 x 1024, 832 x 1216, 1216 x 832, 768 x 1344, 1344 x 768 | About 7 to 10 s of rendering, plus the checkpoint load |

Second pass of **Alta**:

| Parameter | Default | Range | Why |
| --- | --- | --- | --- |
| Upscale | `lanczos`, factor 1.5 | 1.25 to 1.5 | Pixel upscale with no extra model. 1.5 reaches 1248 x 1824, a size the Illustrious card names |
| `denoise` | 0.45 | 0.3 to 0.6 | Adds detail and keeps the composition |
| Steps | 14 | 8 to 30 | ComfyUI runs this many steps over the last part of the schedule |
| Sampler, scheduler | `dpmpp_2m`, `karras` | Allowlist | A non-ancestral sampler keeps the first pass's structure |
| CFG, seed, prompts | Same as the first pass | | |

Times are estimates until P05 measures them. The [preset rule](decisions/0026-two-pass-image-rendering.md) keeps
**Alta** as the default if the 95th percentile of the render stage is at most 45 s.

### Measured levers

P05 measures three levers on the default image profile, with the scenes of the image identity protocol and blind pairs
reviewed by the owner ([ADR 0036](decisions/0036-structured-image-scenes-and-measured-levers.md)):

| Lever | Values | Adopted when |
| --- | --- | --- |
| `PerturbedAttentionGuidance` on the first pass | `scale` 0, 1.5 and 3 | Its images win at least 60 % of the blind pairs against 0, and the **Alta** p95 stays within 45 s. Adoption creates new workflow versions |
| Identity weight | 1.1 on `hair_color` and `eye_color` | The protocol's attribute accuracy rises by at least 5 points, with no visible style change |
| Tall and wide sizes | Kept or removed | Kept if their grid images show no more visible defects than portrait and landscape |

### Grid

P05 renders a fixed grid for each image profile: 3 characters, 2 scenes and 3 seeds, with CLIP skip 1 and 2, CFG 4.5,
5.5 and 7, and second-pass `denoise` 0.35, 0.45 and 0.55. The defaults stay unless the grid shows a visible defect. The
owner reviews the profiles blind for the [default rule](models.md#defaults-and-how-they-can-change). The result goes to
`docs/verification/`.

## Workflow

Workflows live in `workflows/comfyui/`, in ComfyUI API format. Typed code sets values on the parsed JSON. It never edits
text, so a prompt cannot change the graph.

| File | Use |
| --- | --- |
| `sdxl_base.v2.json` | **Rapida**. One pass |
| `sdxl_hires.v1.json` | **Alta**. Two passes |
| `sdxl_hires_tiled.v1.json` | **Alta** retry after out of memory. Tiled decode and encode |
| `sdxl_base_tiled.v2.json` | **Rapida** retry after out of memory. Tiled decode |

`sdxl_hires.v1.json`:

```json
{
  "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "Illustrious-XL-v2.0.safetensors"}},
  "2": {"class_type": "CLIPSetLastLayer", "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -2}},
  "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
  "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
  "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
  "6": {"class_type": "KSampler", "inputs": {
    "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
    "seed": 0, "steps": 28, "cfg": 5.5, "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": 1.0
  }},
  "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
  "9": {"class_type": "ImageScaleBy", "inputs": {"image": ["7", 0], "upscale_method": "lanczos", "scale_by": 1.5}},
  "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["1", 2]}},
  "11": {"class_type": "KSampler", "inputs": {
    "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["10", 0],
    "seed": 0, "steps": 14, "cfg": 5.5, "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 0.45
  }},
  "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
  "8": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "lyrveil"}}
}
```

- `sdxl_base.v2.json` is nodes 1 to 8 of the same graph, with node 8 saving node 7. Version 1 had the same nodes with
  `ckpt_name` fixed to v1.0. Version 2 takes `ckpt_name` from the profile.
- `ckpt_name` always comes from the catalog or from a registered custom profile, never from user text.
- The tiled files replace `VAEDecode` with `VAEDecodeTiled` and `VAEEncode` with `VAEEncodeTiled` (`tile_size` 512,
  `overlap` 64, `temporal_size` 64, `temporal_overlap` 8).
- Both passes share the seed. Final width and height are the first-pass size times 1.5, rounded to a multiple of 8.
- If PAG is adopted, new workflow versions insert `PerturbedAttentionGuidance` between node 1 and the first `KSampler`. The
  versions in this table stay valid for stored images.
- Lyrveil only submits its own files. The API offers no way to send a custom workflow.
- `/system_stats` is read at readiness and `comfyui_version` is stored with each image.

## Progress

| Phase | Source | Italian label |
| --- | --- | --- |
| `prompt` | Job stage | Sta preparando l'immagine… |
| `waiting` | Lifecycle state | In attesa della scheda video… |
| `loading` | `executing` with node `1` | Sta preparando i pennelli… |
| `sampling` | `progress` of node `6`, with `value` and `max` | Sta disegnando… 12/28 |
| `refining` | `progress` of node `11`, **Alta** only | Sta rifinendo i dettagli… 6/14 |
| `saving` | Job stage | Quasi fatto… |

Percentages come only from real `progress` values. Other phases show an indeterminate indicator.

## Persistence

```text
data/media/
  .staging/<job_id>/...              Removed at startup when unfinished
  images/YYYY/MM/<image_id>.png      Original. About 2 to 3 MB for Rapida, 4 to 6 MB for Alta
  thumbs/YYYY/MM/<image_id>.webp     Longest side 512 px
  assets/<character_id>/<asset_id>.<ext>
```

Steps:

1. Fetch from `GET /view` into staging over the internal network.
2. Validate with Pillow and compute the SHA-256.
3. Write the thumbnail.
4. Move with `os.replace` to the final path, on the same filesystem.
5. In one transaction: insert `generated_images` with the scene, aspect and outfit, link the variant, set the message
   `complete`, mark the job `completed`.
6. Publish `image.completed`.

`generated_images.job_id` is unique, so repeating step 5 cannot duplicate an image. The file name comes from the identifier and never
from model or user text.

ComfyUI's directories live in a tmpfs that disappears when the container stops. The backend serves `/media/...` from `data/media`, so
images never depend on ComfyUI.

`--disable-metadata` keeps prompts out of the PNG, so a shared image carries no private text. Metadata stays in the database and in
exports.

## Regeneration

`POST /images/{id}/regenerate` creates a new job with the stored prompt, skips the `prompt` stage and uses a new seed unless `keep_seed` is
true. In a conversation the result is a new variant of the same message, with the same arrows as text alternatives.

The gallery's advanced dialog can edit positive and negative prompts. The [sanitizer](#sanitizer) rules for user weights apply, the
safety negative is always appended and the guard always runs.

## Failures

| Failure | Handling |
| --- | --- |
| Checkpoint file missing or with the wrong size | The job fails with `MODEL_FILE_MISSING` before the mode change. The message names the profile and the download command |
| Model returns an invalid scene | Repair attempt, then the fallback scene: `upper body`, the usual outfit, and the words of the request that match the vocabulary |
| Caption fails | The image is sent without text |
| `IMAGE_OOM` | Lease ends with `oom`. Runtime restarts. One retry with the tiled file of the same preset. If **Alta** fails again, one last try with **Rapida**. The message says which preset produced the image |
| Stalled, no progress for 120 s | `POST /interrupt`, retry once |
| Total time over 300 s | Same |
| `GPU_INSUFFICIENT_VRAM` | The job fails after the wait. The message shows a clear reason and a retry action |
| User cancels | `POST /interrupt` if rendering. The placeholder is removed and the mode returns to text |
| Crash during `persist` | The job restarts from the staged file |

The text model always comes back, whether the image succeeded or not.

## Prepared extensions

Not active in 1.0:

| Extension | Prepared by |
| --- | --- |
| img2img variations of the primary avatar | Core nodes only (`LoadImage`, `VAEEncode`, `KSampler` with `denoise` below 1). Needs `POST /upload/image` in [Runtime contracts](runtime-contracts.md). First candidate after 1.0 |
| LoRA | `character_visual_profiles.loras_json` and a workflow template slot. The model card states that modules trained on Illustrious v0.1 work with v1.0 |
| Reference images, IP-Adapter, ControlNet | `reference_asset_ids_json`, stored assets. They need extra models, so [R-MOD-06](requirements.md#4-models) and the non-goals must change first |
| Face and hand detailer | Needs a detection model and custom nodes. See [ADR 0026](decisions/0026-two-pass-image-rendering.md) |
| v-prediction checkpoints | `prediction` in the catalog. 1.0 accepts only `epsilon` |

Other checkpoints are already selectable through [image profiles](models.md#image-profiles). Custom nodes needed for
any extension would require an ADR, because `--disable-all-custom-nodes` is part of the security model.
