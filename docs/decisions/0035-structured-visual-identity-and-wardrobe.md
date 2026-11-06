# ADR 0035: Structured visual identity, wardrobe and tag vocabulary

Status: accepted (2026-11-06). Supersedes: none.

## Context
- `persistent_prompt_en` is free English text written by `visual_profile_translate`. Its quality, tag choice and order depend on the active text model. Two profiles translating the same card give different identity tags, so a character's images drift when the text model changes.
- The model's 24 free scene tags pass a filter, but ComfyUI v0.43.0 parses prompt syntax in any text. This was read in `comfy/sd1_clip.py` on 2026-11-06:
  - `(text)` multiplies the weight by 1.1, and `(text:1.5)` sets it.
  - `\(` and `\)` are literal parentheses.
  - A word that starts with `embedding:` loads an embedding file. A missing file only logs a warning.

  Scene text can therefore re-weight or suppress identity.
- The encoder splits the prompt into chunks of 75 tokens plus start and end tokens, encodes each chunk and concatenates them. SDXL's pooled vector comes from the first chunk only, so the tags written first condition the whole image the most.
- Age reaches images only as `adult`. Anime checkpoints draw most characters young. That misrepresents a character of 52 or 70 and weakens the adult-only safeguard.
- Clothing is one description. When a character changes outfit, the model writes free scene tags that differ each time.
- The NoobAI-XL card orders caption tags as subject count, character, series, artists, special tags, general tags, then other tags.

## Decision
- **`identity_en`** replaces `persistent_prompt_en`:
  - closed values from the vocabulary: `hair_color`, `hair_length` from `very short hair` to `very long hair`, `eye_color`, and `skin` (`pale skin`, `tan`, `dark skin`, or none);
  - lists of up to 4 vocabulary tags: `hairstyle`, `face`, `body`, `marks`, `accessories`, `style`;
  - `extra`: up to 6 free tags of up to 4 words each.
- **Wardrobe.** The usual outfit gets English tags from `clothing`: `usual_outfit_en`, up to 12 tags. Up to 5 named outfits each hold a key, an Italian label, an Italian description and their tags.
- **Age band.** It is derived from `age` and `subject_tag`, and always follows `adult`:

  | Age | `1girl` | `1boy` | `1other` |
  | --- | --- | --- | --- |
  | 18 to 29 | `woman` | `man` | `person` |
  | 30 to 44 | `mature female` | `mature male` | `mature` |
  | 45 to 64 | `mature female, middle-aged` | `mature male, middle-aged` | `mature, middle-aged` |
  | 65 and over | `old woman, wrinkles` | `old man, wrinkles` | `old, wrinkles` |

  These are starting values. T-QLT-07 checks them, and the P05 verification note records any change.
- **Tag vocabulary.** The file is `backend/src/lyrveil/images/vocabulary/tags.v1.json`. It is an original list of tags with category, Italian aliases and CLIP token count, released under Apache 2.0 with the code. It holds no text copied from tag wikis.
- **Fixed order** of the positive prompt:
  1. quality tags;
  2. rating;
  3. subject tag;
  4. `adult` and the age band;
  5. identity: hair color, hair length, hairstyle, eye color, skin, face, body, marks;
  6. outfit, then accessories;
  7. scene: expression, pose, framing, angle, location, time of day, lighting, objects;
  8. style;
  9. extra.
- **Caps.** Identity 16 tags, outfit and accessories 12, scene 18, style 4, extra 6.
- **Token counting.** The builder counts tokens with the vocabulary's counts, two per word for free tags, plus one per comma. It records whether identity ends inside the first chunk, and the Creator warns when it would not.
- **Sanitizer** for every tag that comes from a model or a user:
  - only letters, digits, spaces, hyphens, apostrophes and periods are kept;
  - `embedding:` is removed;
  - only the builder writes weights, and vocabulary tags use only the characters the sanitizer keeps, so none needs an escape;
  - in the gallery's advanced dialog, user weights are clamped to 0.5 to 1.5, and `embedding:` is refused.
- **Storage.** `character_visual_profiles` gains `identity_en_json`, `usual_outfit_en_json` and `outfits_json`, and loses `persistent_prompt_en`. The interface shows the rendered identity as **Prompt persistente**.
- **Locks** map to slots:

  | Locked field | Slots |
  | --- | --- |
  | `hair` | The hair slots |
  | `eyes` | `eye_color` |
  | `face` | `face`, `skin` |
  | `body` | `body` |
  | `distinctive_marks` | `marks` |
  | `accessories` | `accessories` |
  | `art_style` | `style` |
  | `clothing` | The usual outfit |

  The translation job never rewrites a locked slot, and scene tags of its category are removed.
- `visual_profile_translate` returns `VisualIdentityResult` under a grammar built from the vocabulary's closed values. The user can edit every slot.

## Alternatives
- **Keep free text with a better template.** It still depends on each model's vocabulary and order.
- **Prompt weights on the whole identity.** That is stronger, but it over-saturates and shifts the style. Weights stay a measured lever for two slots ([ADR 0036](0036-structured-image-scenes-and-measured-levers.md)).
- **LoRA or IP-Adapter per character.** That gives the strongest consistency, but it needs extra models or custom nodes. R-MOD-06 and the security model exclude both in 1.0.

## Consequences
- Identity tags are byte-identical for every text profile (T-IMG-15). The text model still writes scenes, through the schema of ADR 0036.
- The vocabulary is a maintained data file with its own tests (T-IMG-16). Free tags in `extra` cover what it lacks.
- Images of older characters look their age as far as the checkpoint can draw it. T-QLT-07 measures this.
