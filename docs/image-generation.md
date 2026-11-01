# Image generation

Status: accepted (2026-11-01). Implementation: planned, phase P05.

Images are normal messages. The text model prepares the prompt, ComfyUI renders it, and the backend stores the result
independently of the runtime. The GPU hand-over is described in [GPU orchestration](gpu-orchestration.md).

## Modes

### Manual mode

The user asks for an image in two ways:

1. **Explicit action.** The composer's image button opens a small form with the request, aspect and optional seed, steps and
   CFG. It calls `POST /conversations/{id}/images`.
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
| `prompt` | `llm` | Generates `ImagePromptResult`, builds the final prompt, runs the content guard | `payload_json.final_prompt`, `payload_json.caption`, `seed` |
| `render` | `image` | Submits the workflow, follows progress, fetches the file into staging | Staged file path |
| `persist` | `none` | Validates, writes the thumbnail, moves files, updates the message | `generated_images`, the variant |

The seed is chosen and saved in `prompt`, so a retried `render` reproduces the same request.

## Visual PromptBuilder

`VisualConsistencyService` combines a deterministic part with the model's scene contribution.

### Input to the model

The visual profile, setting, recent messages, the user's request and the conversation language. The output is in English except
the caption:

```python
class ImagePromptResult(BaseModel):
    schema_version: Literal[1]
    scene_tags: Annotated[list[str], Field(max_length=24)]    # Setting, action, framing, lighting
    extra_negative: Annotated[list[str], Field(max_length=8)]
    aspect: Literal["square", "portrait", "landscape"]
    caption: Annotated[str, StringConstraints(max_length=280)] # Short message in the character's voice
```

The caption becomes the text of the message when the image is ready, which produces a message with text and image from a single
model call. The request is in Italian, and the prompt is prepared automatically before the GPU changes hands.

### Final prompt

```text
positive = quality tags, rating tag, subject tag, "adult", persistent identity tags,
           scene tags after filtering, style tags
negative = base negative, safety negative, persistent negative, extra negative after filtering
```

| Part | Default value |
| --- | --- |
| Quality tags | `masterpiece, best quality, amazing quality` |
| Rating tag | `general` |
| Base negative | `worst quality, bad quality, low quality, lowres, bad anatomy, bad hands, missing fingers, extra digits, jpeg artifacts, signature, watermark, text, username` |
| Safety negative | `child, loli, shota, toddler, underage, minor, nsfw, explicit, nude`. Always appended |

### Scene tag filter

Each model tag is processed in order:

1. Lowercase, trim, replace `_` with a space, split on commas.
2. Reject non-ASCII text, more than 6 words, duplicates and repeated identity tags.
3. Reject blocked terms. A hit fails the job with `CONTENT_POLICY_BLOCKED`.
4. Remove tags about hair color, eye color and skin tone. These belong to the profile.
5. When the `clothing`, `accessories` or `distinctive_marks` field is locked, remove tags that change it.

## Visual consistency

| Aspect | Mechanism |
| --- | --- |
| Hair, eyes, physical features, marks, accessories, style | `persistent_prompt_en` goes into every prompt. Scene tags cannot override it |
| Recurring clothing | Part of the persistent prompt when relevant. A scene can add clothing unless the field is locked |
| Profile changes | A changed description starts `visual_profile_translate`. The result replaces `persistent_prompt_en` only if the user has not locked it |
| Seed | Policy `random`, `fixed` or `base_offset`. Stored for every image |

The English tags are generated once and stay editable, so each image does not need a new translation.

**Limit:** the seed alone does not preserve a character. Different prompts or sizes produce different images even with the same seed.
Tags improve consistency but cannot guarantee it. True consistency needs reference techniques, which 1.0 prepares but does not activate.

## Default parameters

| Parameter | Default | Range | Source |
| --- | --- | --- | --- |
| Square | 1024 x 1024 | Fixed preset | Requirements |
| Portrait | 832 x 1216 | Fixed preset | SDXL bucket |
| Landscape | 1216 x 832 | Fixed preset | SDXL bucket |
| Batch size | 1 | Fixed | Requirements |
| Seed | Random 32-bit, stored | 0 to 4294967295 | Requirements |
| Steps | 28 | 10 to 50 | Community guidance |
| CFG | 5.5 | 1 to 12 | Community guidance |
| Sampler | `euler_ancestral` | Allowlist from ComfyUI's `KSAMPLER_NAMES` | Community guidance |
| Scheduler | `normal` | `normal`, `karras` | Project default |
| CLIP skip | 2 | 1 or 2 | Community guidance |

The model card gives no sampler, steps or CFG. These defaults come from third-party guides for Illustrious XL, which agree on Euler
ancestral, 20 to 28 steps, CFG from 3 to 7 and CLIP skip 2. They are not official.

P05 renders a fixed grid of 3 characters, 2 scenes and 3 seeds with CLIP skip 1 and 2, and CFG 4.5, 5.5 and 7. The defaults stay unless
the grid shows a visible defect. The result goes to `docs/verification/`.

## Workflow

`workflows/comfyui/sdxl_txt2img.v1.json`, in ComfyUI API format:

```json
{
  "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "Illustrious-XL-v1.0.safetensors"}},
  "2": {"class_type": "CLIPSetLastLayer", "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -2}},
  "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
  "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
  "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
  "6": {"class_type": "KSampler", "inputs": {
    "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
    "seed": 0, "steps": 28, "cfg": 5.5, "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": 1.0
  }},
  "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
  "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "lyrveil"}}
}
```

- Typed code sets values on the parsed JSON. It never edits text, so a prompt cannot change the graph.
- `sdxl_txt2img_tiled.v1.json` replaces node 7 with `VAEDecodeTiled` (`tile_size` 512, `overlap` 64, `temporal_size` 64,
  `temporal_overlap` 8). It is the out-of-memory retry.
- Lyrveil only submits its own files. The API offers no way to send a custom workflow.
- `/system_stats` is read at readiness and `comfyui_version` is stored with each image.

## Progress

| Phase | Source | Italian label |
| --- | --- | --- |
| `prompt` | Job stage | Sta preparando l'immagine… |
| `waiting` | Lifecycle state | In attesa della scheda video… |
| `loading` | `executing` with node `1` | Sta preparando i pennelli… |
| `sampling` | `progress` with `value` and `max` | Sta disegnando… 12/28 |
| `saving` | Job stage | Quasi fatto… |

Percentages come only from real `progress` values. Other phases show an indeterminate indicator.

## Persistence

```text
data/media/
  .staging/<job_id>/...              Removed at startup when unfinished
  images/YYYY/MM/<image_id>.png      Original
  thumbs/YYYY/MM/<image_id>.webp     Longest side 512 px
  assets/<character_id>/<asset_id>.<ext>
```

Steps:

1. Fetch from `GET /view` into staging over the internal network.
2. Validate with Pillow and compute the SHA-256.
3. Write the thumbnail.
4. Move with `os.replace` to the final path, on the same filesystem.
5. In one transaction: insert `generated_images`, link the variant, set the message `complete`, mark the job `completed`.
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

The gallery's advanced dialog can edit positive and negative prompts. The safety negative is always appended and the guard always runs.

## Failures

| Failure | Handling |
| --- | --- |
| Model returns invalid JSON | Repair attempt, then a fallback prompt built from the persistent tags and the request's words filtered by the guard, without a caption |
| `IMAGE_OOM` | Lease ends with `oom`. Runtime restarts. One retry with tiled decode |
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
| LoRA | `character_visual_profiles.loras_json` and a workflow template slot. The model card states that modules trained on Illustrious v0.1 work with v1.0 |
| Reference images, IP-Adapter, ControlNet | `reference_asset_ids_json`, stored assets. ComfyUI's `POST /upload/image` enters [Runtime contracts](runtime-contracts.md) only when needed |
| Other checkpoints | `LYRVEIL_IMAGE_CHECKPOINT`, `ImageRuntime` adapter |

Custom nodes needed for any of these would require an ADR, because `--disable-all-custom-nodes` is part of the security model.
