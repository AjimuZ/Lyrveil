# ADR 0036: Structured image scenes, sizes, captions and measured image levers

Status: accepted (2026-11-06). Supersedes: none.

## Context
- `ImagePromptResult` asks the text model for up to 24 free scene tags, an aspect and a caption in one call. Each profile frames, poses and lights differently, and free tags can contradict the identity.
- The caption comes from the structured task at temperature 0.3, outside the character's voice, anchor and normalizer, so it often reads like a description of the image.
- Only three sizes exist. A full-body figure is small in 832 × 1216, and wide scenes have only 1216 × 832. The NoobAI-XL card lists seven sizes: 768 × 1344, 832 × 1216, 896 × 1152, 1024 × 1024, 1152 × 896, 1216 × 832 and 1344 × 768.
- ComfyUI v0.43.0 has `PerturbedAttentionGuidance`, read in `comfy_extras/nodes_pag.py` on 2026-11-06. It is a built-in extra node, with inputs `model` and `scale` (default 3.0), and it adds one model evaluation per step. The method's authors report better structure. Whether it helps these checkpoints is unknown until it is measured.

## Decision
- **`ImageScene`** replaces `ImagePromptResult`. It has no caption:

  ```python
  class ImageScene(BaseModel):
      schema_version: Literal[1]
      framing: Literal["close-up", "portrait", "upper body", "cowboy shot", "full body", "wide shot"]
      angle: Literal["straight-on", "from side", "from behind", "from above", "from below", "pov", "dutch angle"] | None
      expression: Annotated[list[str], Field(max_length=3)]      # Vocabulary tags
      pose: Annotated[list[str], Field(max_length=4)]            # Vocabulary tags
      outfit: str                                                # A wardrobe key or "scene"
      scene_clothing: Annotated[list[str], Field(max_length=6)]  # Only when outfit is "scene"
      location: Annotated[list[str], Field(max_length=6)]
      time_of_day: Literal["day", "sunrise", "sunset", "twilight", "night"] | None
      lighting: Annotated[list[str], Field(max_length=3)]        # Vocabulary tags
      objects: Annotated[list[str], Field(max_length=4)]
      extra_negative: Annotated[list[str], Field(max_length=6)]
  ```

  - A grammar built for each request limits `outfit` to the character's wardrobe keys and `scene`. The template is `image_scene.v1.j2`.
  - Every tag passes the sanitizer of [ADR 0035](0035-structured-visual-identity-and-wardrobe.md). Free tags in `location`, `objects`, `scene_clothing` and `extra_negative` have at most 4 words.
  - Tags in an identity category, such as hair, eyes or skin, are removed.
- **Aspect `auto`** is the new default. It derives the size from `framing`:

  | `framing` | Aspect | First pass | **Alta** |
  | --- | --- | --- | --- |
  | `close-up`, `portrait`, `upper body`, `cowboy shot` | `portrait` | 832 × 1216 | 1248 × 1824 |
  | `full body` | `tall` | 768 × 1344 | 1152 × 2016 |
  | `wide shot` | `wide` | 1344 × 768 | 2016 × 1152 |

  Avatars use `square`. The user can still choose `square`, `portrait`, `landscape`, `tall` or `wide`.
- **Caption in the character's voice.** The `prompt` stage makes two text calls before the GPU changes hands:
  1. The caption, through the normal reply path: stable prefix, history, dynamic context and anchor. The dynamic context gets one extra line: "Stai per mandare a {{user}} un'immagine: {{request}}. Scrivi solo il messaggio che la accompagna." This call goes first, while the chat prompt is still cached. The normalizer and the validators apply, and the band's `max_length` is capped at 120.
  2. The scene, under its grammar.

  If the caption fails, the image is sent without text.
- **Measured levers in P05.** They run on the default image profile, with the T-QLT-07 scenes and blind pairs reviewed by the owner:
  - **PAG** on the first pass, with `scale` 0, 1.5 and 3. A value is adopted if its images win at least 60 % of the blind pairs against 0, and the **Alta** p95 stays within 45 s. Adoption creates new workflow versions.
  - **Identity weight** 1.1 on `hair_color` and `eye_color`. It is adopted if the T-QLT-07 attribute accuracy rises by at least 5 points, with no visible style change in the pairs.
  - **`tall` and `wide`** stay if their grid images show no more visible defects than `portrait` and `landscape`.
- **Image identity protocol T-QLT-07:**
  - the 3 demo characters, plus 2 synthetic test characters aged 52 and 70;
  - 4 scenes and 3 seeds, with scenes written by every catalog text profile;
  - the owner checks hair color and length, eye color, age band and usual outfit on a fixed sheet;
  - targets: at least 90 % of the checks correct, and no profile more than 5 points below the default.
- `generated_images` gains `scene_json`, `aspect` and `outfit_key`.
- **Fallback scene.** If the model's output is still invalid after the repair, the builder uses `upper body`, the usual outfit and the words of the request that match the vocabulary.

## Alternatives
- **Free scene tags with a better filter.** Framing and wording would still depend on the model.
- **A caption from the structured call at a higher temperature.** It would still bypass the anchor and the normalizer.
- **An upscale model or a detailer for sharpness.** Both need extra models or custom nodes ([ADR 0026](0026-two-pass-image-rendering.md)).
- **img2img variations from the primary avatar.** This works with core nodes, but it changes the flow of every request. It is planned after 1.0.

## Consequences
- Composition, size and outfit follow the scene schema on every profile. Only free location and object words still vary with the model.
- The `prompt` stage makes two text calls. T-IMG-17 and T-IMG-18 cover the schema, the aspect table and the caption path.
- Workflows gain the `tall` and `wide` sizes. PAG enters only through new workflow versions, after the measurement.
