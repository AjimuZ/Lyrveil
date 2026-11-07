# Character system

Status: accepted (2026-11-01), revised 2026-11-06 for the voice fields and the structured visual identity. Implementation:
phase P03. Delivered in P03.1a: the card model (`lyrveil.domain.card`), its validation against the tag vocabulary
(`lyrveil.services.card_rules`), storage, and `POST /characters` and `GET /characters/{id}`. Delivered in P03.1b: saving
with `PUT /characters/{id}`, versions, field-level diff, restore, and the locks against drafts and proposals
([Versions and locks](#versions-and-locks)). Delivered in P03.2a: the Creator screens for creating and editing a card
(`/characters/new`, `/characters/:id/edit`; see [Frontend screen 3](frontend.md#3-character-creator)) and
`GET /characters/vocabulary`. Delivered in P03.2b: the profile ([Frontend screen 4](frontend.md#4-character-profile)),
duplicate, archive and purge ([Duplicate, archive, purge](#duplicate-archive-purge)). Delivered in P03.3: assisted
creation ([Assisted creation](#assisted-creation)), without the content guard. Planned: locks in the translation job,
assets, the content guard (P03.6), and the exchange format.

The Character Creator is a central feature. It creates, edits, duplicates, imports, exports and deletes characters, and
it uses the same text and image infrastructure as conversations.

## Card schema

The card is one Pydantic model used by the API, the versions and the exchange format. Text is primary. Numbers
appear only for age. The fields that keep the voice and the look independent of the active models are described in
[ADR 0033](decisions/0033-card-fields-for-a-model-independent-voice.md) and
[ADR 0035](decisions/0035-structured-visual-identity-and-wardrobe.md).

```python
class Identity(BaseModel):
    first_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    last_name: str | None = None
    age: Annotated[int, Field(ge=18, le=120)]
    grammatical_gender: GrammaticalGender = "non_indicato"   # femminile, maschile, non_indicato
    narrative_identity: str = ""     # Who they are in the story
    biography: str = ""
    occupation: str = ""
    interests: list[str] = []
    background: str = ""
    setting: str = ""                # Place or world
    primary_language: LanguageCode = "it"


class Trait(BaseModel):
    category: TraitCategory          # main_trait, value, motivation, preference, flaw, fear,
                                     # habit, humor, temperament, relational_style
    description: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    locked: bool = False


class ExampleExchange(BaseModel):
    user: Annotated[str, StringConstraints(max_length=300)] | None = None    # The message it answers
    character: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class CommunicationProfile(BaseModel):
    formality: Formality = "informale"              # molto_informale, informale, neutra, formale
    message_length: MessageLength = "brevi"         # brevissimi, brevi, medi, lunghi. Sets the length band
    question_frequency: Frequency = "a_volte"       # raramente, a_volte, spesso
    punctuation: str = ""                           # "Poche virgole, molti puntini"
    colloquial_style: str = ""                      # "Modi di dire toscani, ironia asciutta"
    expressiveness: Expressiveness = "media"        # contenuta, media, vivace
    initiative_frequency: Frequency = "a_volte"     # Used by the proactive evaluation
    address_form: Literal["tu", "lei"] = "tu"
    narration: Literal["nessuna", "leggera", "libera"] = "nessuna"
    emoji: Literal["mai", "raramente", "a_volte", "spesso"] = "raramente"
    examples: Annotated[list[ExampleExchange], Field(max_length=8)] = []
    recurring_expressions: Annotated[list[str], Field(max_length=12)] = []
    avoid: Annotated[list[Annotated[str, StringConstraints(max_length=120)]], Field(max_length=8)] = []


class VisualIdentityEn(BaseModel):
    hair_color: HairColor | None = None      # Closed values from the tag vocabulary
    hair_length: HairLength | None = None    # very short hair, short hair, medium hair, long hair, very long hair
    eye_color: EyeColor | None = None
    skin: Literal["pale skin", "tan", "dark skin"] | None = None
    hairstyle: Annotated[list[VocabularyTag], Field(max_length=4)] = []
    face: Annotated[list[VocabularyTag], Field(max_length=4)] = []
    body: Annotated[list[VocabularyTag], Field(max_length=4)] = []
    marks: Annotated[list[VocabularyTag], Field(max_length=4)] = []
    accessories: Annotated[list[VocabularyTag], Field(max_length=4)] = []
    style: Annotated[list[VocabularyTag], Field(max_length=4)] = []
    extra: Annotated[list[FreeTag], Field(max_length=6)] = []      # Up to 4 words each


class Outfit(BaseModel):
    key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,23}$")]   # Not "usual" or "scene"
    label: Annotated[str, StringConstraints(min_length=1, max_length=40)]       # Italian, shown in the image form
    description: Annotated[str, StringConstraints(max_length=300)] = ""          # Italian
    tags_en: Annotated[list[FreeTag], Field(max_length=12)] = []


class VisualProfile(BaseModel):
    face: str = ""
    hair: str = ""
    eyes: str = ""
    body: str = ""
    clothing: str = ""               # The usual outfit, key "usual"
    accessories: str = ""
    distinctive_marks: str = ""
    art_style: str = ""
    subject_tag: Literal["1girl", "1boy", "1other"] = "1other"
    identity_en: VisualIdentityEn = VisualIdentityEn()
    usual_outfit_en: Annotated[list[FreeTag], Field(max_length=12)] = []
    outfits: Annotated[list[Outfit], Field(max_length=5)] = []
    persistent_negative_en: str = ""
    locked_fields: list[str] = []
    seed_policy: Literal["random", "fixed", "base_offset"] = "random"
    base_seed: int | None = None


class CharacterCard(BaseModel):
    identity: Identity
    traits: list[Trait] = []
    communication: CommunicationProfile = CommunicationProfile()
    visual: VisualProfile = VisualProfile()
```

`VocabularyTag` must exist in the [tag vocabulary](image-generation.md#tag-vocabulary) under the slot's category.
`FreeTag` passes the [sanitizer](image-generation.md#sanitizer) and has at most 4 words.

The Italian enum values appear in the interface as they are. They reach prompts through the `words` filter, which
replaces `_` with a space.

## Manual creation

The form has four tabs matching the requirements: **Identità**, **Personalità**, **Comunicazione**, **Aspetto**. Only
first name and age are required. The form uses Angular Signal Forms and saves a local draft in the browser.

Rules:

- `age` is required and at least 18. The interface explains that all characters are adults.
- **Identità** asks how the character speaks of itself: **Al femminile**, **Al maschile** or **Non indicato**. While
  `subject_tag` is still the default, the choice also proposes the matching tag.
- Each personality category accepts several descriptive entries.
- **Comunicazione** offers *tu* or *Lei*, narration and emoji as choices. It proposes *Lei* when the register is
  `formale`. Examples are pairs: an optional message from the user and the character's answer. They reach the prompt in
  saved order while block 3 stays within 500 tokens, and the editor shows how many fit. **Da evitare** lists
  up to 8 phrases the character never says.
- **Aspetto** shows avatar actions, the identity slots, the usual outfit and up to 5 named outfits. Saving starts the
  English tag translation for the slots that are not locked, and shows the rendered identity as **Prompt persistente**.
  A warning appears when the identity would not fit in the first prompt chunk.
- Saving creates version 1 and an empty visual profile if none exists.

Delivered in P03.2a: all four tabs, the draft in the browser and the validation above. In **Aspetto** the slots are
chosen by hand from the vocabulary and nothing is translated on save; **Prompt persistente** is the plain list of the
chosen tags. The avatar actions, the count of examples that fit the prompt and the first-chunk warning are not delivered.

## Versions and locks

Delivered in P03.1b; see [Versioning](consistency-engine.md#versioning) for the rules and [API](api.md#characters) for
the routes.

- Creation writes version 1. A save writes a new version only if the validated card differs from the stored one, so a
  save with no change creates none. Restore always writes one, with the old content.
- The diff compares two snapshots field by field (`lyrveil.domain.card_diff`). Lists compare whole.
- `keep_locked` (`lyrveil.domain.card_locks`) is the rule for anything automatic, a draft over an existing character or
  a proposal. A locked trait stays, first and unchanged; the proposed traits follow, always unlocked, and a repeat of a
  locked trait is dropped. A locked visual field keeps its Italian text and the slots it protects (ADR 0035: `hair`
  holds `hair_color`, `hair_length` and `hairstyle`; `eyes` holds `eye_color`; `face` holds `face` and `skin`;
  `clothing` holds `usual_outfit_en`), and the list of locks does not change. Traits have no identifier, so a lock
  matches by category and description.
- The user's own save carries the locks it wants, so it can change a locked item by unlocking it in the same save.
- Locks in the translation job are not delivered; the job does not exist yet.

## Assisted creation

The user writes a description, such as:

> Crea un personaggio di 26 anni, appassionato di fantasy, con una personalità ironica, un carattere indipendente e un
> modo di scrivere spontaneo.

Flow:

1. `POST /characters/drafts` checks the text with the content guard and starts `character_draft`.
2. The job holds an LLM lease and generates `CharacterDraft`, which has the same fields as `CharacterCard` without
   locks, seeds and English tags. It uses the grammar and the repair attempt.
3. The result is validated. An age under 18 fails the schema, and the response explains why.
4. The result stays in `generation_jobs.result_json`. No character exists yet.
5. The Creator opens with every field filled in and editable. A note marks the fields the model invented.
6. Saving goes through the normal `POST /characters`, which starts the tag translation.

If an image is being generated, the job waits, and the Creator shows the queue state.

Delivered in P03.3, with these limits and choices:

- `POST /characters/drafts` takes `{"description": "...", "character_id": null}` and answers `202` with the job. The
  description is trimmed, must not be blank and has at most 2000 characters. **The content guard does not run yet**: it
  arrives with P03.6, so step 1 is only the length check today. The grammar still keeps the age between 18 and 120.
- `CharacterDraft` (`lyrveil.domain.card_draft`) leaves out `traits.locked`, `visual.identity_en`,
  `visual.usual_outfit_en`, `visual.outfits.tags_en`, `visual.persistent_negative_en`, `visual.locked_fields`,
  `visual.seed_policy` and `visual.base_seed` (`DRAFT_EXCLUDED_FIELDS`). Its grammar is generated from the model at
  start-up by `schema_to_gbnf` (`lyrveil.prompts.gbnf`), and every key is written, so the model fills every field.
  A unit test fails when the draft stops mirroring the card or the grammar stops matching the draft.
- The handler (`CharacterCreatorService`, `lyrveil.services.character_creator`) runs under the llm lease the scheduler
  holds. It validates the output as `CharacterDraft`, then as a card with `validate_card`, and repairs once with the
  Italian reason and the refused output in the prompt (`lyrveil.prompts.structured`). A second refusal fails the job
  with `STRUCTURED_OUTPUT_INVALID` and that reason in `error_detail`, without a retry; a runtime failure is retried as
  for chat jobs.
- The result in `result_json` is `{"card", "proposed_fields", "character_id", "attempts"}`: a whole card that
  `POST /characters` accepts as it is, and the dotted paths of the fields the model filled (for a new character the
  name, the age and every field that differs from its default; over a character the fields it changed).
- With `character_id`, the model sees the current card as data and the draft replaces it: `keep_locked` puts back the
  locked traits and visual fields, and the locks, the seed policy, the English slots, the negative prompt and the
  English tags of an outfit whose key stays are kept from the stored card. The English slots therefore do not follow
  a changed Italian description until the tag translation exists. The stored card does not change.
- The prompt is Python (`lyrveil.prompts.character_draft`), not a Jinja file, because the backend has no template
  engine yet. It has the shared shape of [Structured tasks](prompting.md#structured-tasks) and an original worked
  example, which a unit test validates as a draft.

## Avatars and images

| Action | Implementation |
| --- | --- |
| Upload | `POST /characters/{id}/assets`. PNG, JPEG or WebP up to 15 MB. Pillow decodes the bytes, re-encodes without metadata and enforces a 8192 x 8192 limit. |
| Generate | `POST /characters/{id}/assets/generate`. Creates `image_request` with `purpose = avatar`, using the same pipeline as chat. Square by default. |
| Regenerate | Same request with a new seed. Each result is a new variant. |
| Crop | The browser chooses a rectangle. The backend crops with Pillow and saves a new asset at 512 x 512. The original stays. |
| Variants | Every asset stays until deleted. The profile shows them all. |
| Primary | `POST .../primary`. A partial unique index enforces one. |

Uploads show this note: "Carica solo immagini che hai il diritto di usare. Non usare foto di persone reali senza il loro
consenso."

Delivered in P03.4a, backend only (`lyrveil.media`, `CharacterAssetService`, revision `0008`): upload, crop, variants,
thumbnails, primary and delete, with the checks of [Security](security.md#input-validation). An upload keeps its own
format, re-encoded without metadata; every asset also gets a WebP thumbnail, longest side 512 px. Files are named
`assets/<character id>/<asset id>.<ext>` and `<asset id>.thumb.webp` below `data/media`, written through
`data/media/.staging` with an atomic move. The first avatar of a character becomes its primary avatar; another one
becomes primary only through `POST .../primary`, which clears the previous one in the same transaction and keeps
`character_visual_profiles.primary_avatar_asset_id` in step. A crop takes a rectangle inside the image, trims a
non-square one to its centred square, and stores the result as a new asset of the same kind with `crop_json` holding the
rectangle and `source_asset_id`. Deleting the primary avatar is refused with `409` until another is chosen. Not
delivered: generation and regeneration (P05), the upload and crop screens and the note above (frontend), and the copy
of the primary avatar in a duplicate.

Reference images are stored and shown. Version 1.0 workflows do not use them
([Image generation](image-generation.md#prepared-extensions)).

## Duplicate, archive, purge

| Action | Result |
| --- | --- |
| Duplicate | New character with a copy of the card and primary avatar, version 1, actor `duplicate`. No conversations or memories. |
| Archive | Hidden with its conversations. Nothing is deleted. |
| Purge | Preview of counts, confirmation by typing the first name, then a hard delete. See [Deletion policy](data-model.md#deletion-policy). |

Delivered in P03.2b. The copy is named "<first name> (copia)" so the two tell apart in every list, and its history is its
own: version 1, actor `duplicate`. Only the card is copied: avatars exist since P03.4a, but copying the primary avatar
into the duplicate is not delivered yet. Archiving hides the character from the active list and its conversations from the main
conversation list; unarchiving shows both again. A purge deletes, in one transaction, the jobs that name the character,
its conversations or their messages, then variants, messages, conversations, traits, visual profile, versions and the
character, and is refused while one of those jobs runs. Since P03.4a it also deletes the asset rows, and removes their
files after the commit. Memories and images do not exist yet; their deletion is added with them.

## Exchange format

A plain JSON file with extension `.lyrveil-character.json`. It never holds conversations or memories, because those are
private. A complete copy of the data is a [backup](operations.md#backup-and-restore).

```json
{
  "format": "lyrveil.character",
  "format_version": 1,
  "exported_at": "2026-11-01T18:00:00.000Z",
  "generator": {"app": "lyrveil", "version": "1.0.0"},
  "character": {
    "identity": {
      "first_name": "Nerea",
      "last_name": "Falchi",
      "age": 27,
      "grammatical_gender": "femminile",
      "narrative_identity": "Cartografa di una città portuale dove le strade cambiano a ogni marea.",
      "biography": "È cresciuta sopra una bottega di bussole e ha imparato a disegnare prima che a leggere.",
      "occupation": "Cartografa indipendente",
      "interests": ["mappe antiche", "gatti di porto", "caffè troppo forte"],
      "background": "Ha lasciato la gilda dei cartografi dopo una discussione sui confini.",
      "setting": "Velmara, città di mare",
      "primary_language": "it"
    },
    "traits": [
      {"category": "main_trait", "description": "Ironica, curiosa, testarda quando ha ragione.", "locked": false},
      {"category": "fear", "description": "Perdersi in un posto che credeva di conoscere.", "locked": false},
      {"category": "humor", "description": "Battute asciutte, spesso contro se stessa.", "locked": false}
    ],
    "communication": {
      "formality": "informale",
      "message_length": "brevi",
      "question_frequency": "a_volte",
      "punctuation": "Pochi punti esclamativi, qualche puntino.",
      "colloquial_style": "Scrive di getto e si corregge da sola.",
      "expressiveness": "media",
      "initiative_frequency": "a_volte",
      "address_form": "tu",
      "narration": "nessuna",
      "emoji": "raramente",
      "examples": [
        {"user": "Allora, la mappa nuova è pronta?", "character": "ok, ammetto che era al contrario. ma solo un po'"},
        {"user": null, "character": "se passi dal porto portami un caffè, il mio è già finito"}
      ],
      "recurring_expressions": ["ma solo un po'"],
      "avoid": ["Che bella domanda!", "Sono qui per te"]
    },
    "visual": {
      "face": "Viso spigoloso, lentiggini sul naso.",
      "hair": "Capelli rossi corti e spettinati.",
      "eyes": "Occhi verde scuro.",
      "body": "Snella, spalle dritte.",
      "clothing": "Giacca di tela con molte tasche.",
      "accessories": "Bussola d'ottone al collo.",
      "distinctive_marks": "Macchie d'inchiostro sulle dita.",
      "art_style": "Illustrazione anime pulita, colori morbidi.",
      "subject_tag": "1girl",
      "identity_en": {
        "hair_color": "red hair",
        "hair_length": "short hair",
        "eye_color": "green eyes",
        "skin": null,
        "hairstyle": ["messy hair"],
        "face": ["freckles"],
        "body": ["slim"],
        "marks": ["ink stains"],
        "accessories": ["necklace"],
        "style": ["soft colors"],
        "extra": ["brass compass pendant"]
      },
      "usual_outfit_en": ["canvas jacket", "many pockets"],
      "outfits": [
        {
          "key": "festa",
          "label": "Festa del porto",
          "description": "Abito blu scuro con nastri bianchi.",
          "tags_en": ["dark blue dress", "white ribbons"]
        }
      ],
      "persistent_negative_en": "",
      "locked_fields": ["hair", "eyes"],
      "seed_policy": "random",
      "base_seed": null
    }
  },
  "assets": [
    {
      "kind": "avatar",
      "is_primary": true,
      "media_type": "image/png",
      "sha256": "<hash of the decoded bytes>",
      "data_base64": "<optional, present only with include_assets=true>"
    }
  ]
}
```

Nerea Falchi is an original demo character.

### Import rules

- The file limit is 64 MB.
- `format` must match. A `format_version` higher than the supported one is refused with `IMPORT_INVALID`.
- Validation uses the same `CharacterCard` model, so an age under 18 is refused.
- Text is checked by the content guard. English tags pass the sanitizer, and vocabulary slots must hold known tags.
- Unknown fields are refused, so that a typo is not silently lost.
- An import always creates a new character with new identifiers and version 1, actor `import`. It never overwrites.
- Asset bytes follow the same validation as uploads, and hashes must match.
- Imported text is data. It cannot change the global instructions, which precede it in the prompt, and it cannot reach tools,
  because the models have none.

The format has a JSON Schema generated from the model and published at `/api/v1/characters/format-schema`.

Lyrveil never requires this format to run. Any field can be entered by hand.

### Character Card V2

Import of Character Card V2 JSON is planned after 1.0 as a one-way adapter. See [ADR 0021](decisions/0021-character-format-and-card-v2.md).

## Demo characters

The P03 fixture `tests/fixtures/characters/` holds three original characters with different registers, used by tests, the quality
protocols and README screenshots:

| Name | Age | Concept | Register | Gender, address form | Narration, emoji |
| --- | --- | --- | --- | --- | --- |
| Nerea Falchi | 27 | Cartographer in a shifting port city | Informal, dry irony | `femminile`, `tu` | `nessuna`, `raramente` |
| Ilario Sant'Elmo | 41 | Night-train conductor and amateur astronomer | Polite, measured, long sentences | `maschile`, `lei` | `leggera`, `mai` |
| Tessa Moravi | 23 | Apprentice alchemist and part-time barista | Very informal, lively, short bursts | `femminile`, `tu` | `nessuna`, `spesso` |

The first start asks whether to add them. The default answer is no, so a new installation holds no invented data.

`tests/fixtures/characters/quality/` also holds two synthetic test characters, aged 52 and 70, for the
[image identity protocol](image-generation.md#image-identity-protocol). They are never offered to users.
