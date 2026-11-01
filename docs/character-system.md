# Character system

Status: accepted (2026-11-01). Implementation: planned, phase P03.

The Character Creator is a central feature. It creates, edits, duplicates, imports, exports and deletes
characters, and it uses the same text and image infrastructure as conversations.

## Card schema

The card is one Pydantic model used by the API, the versions and the exchange format. Text is primary. Numbers
appear only for age.

```python
class Identity(BaseModel):
    first_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    last_name: str | None = None
    age: Annotated[int, Field(ge=18, le=120)]
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


class CommunicationProfile(BaseModel):
    formality: Formality = "informale"              # molto_informale, informale, neutra, formale
    message_length: MessageLength = "brevi"         # brevissimi, brevi, medi, lunghi
    question_frequency: Frequency = "a_volte"       # raramente, a_volte, spesso
    punctuation: str = ""                           # "Poche virgole, molti puntini"
    colloquial_style: str = ""                      # "Modi di dire toscani, ironia asciutta"
    expressiveness: Expressiveness = "media"        # contenuta, media, vivace
    initiative_frequency: Frequency = "a_volte"     # Used by the proactive evaluation
    examples: Annotated[list[str], Field(max_length=8)] = []
    recurring_expressions: Annotated[list[str], Field(max_length=12)] = []


class VisualProfile(BaseModel):
    face: str = ""
    hair: str = ""
    eyes: str = ""
    body: str = ""
    clothing: str = ""
    accessories: str = ""
    distinctive_marks: str = ""
    art_style: str = ""
    subject_tag: Literal["1girl", "1boy", "1other"] = "1other"
    persistent_prompt_en: str = ""
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

The Italian enum values appear in the interface as they are. They also reach prompts directly, which keeps the
templates simple.

## Manual creation

The form has four tabs matching the requirements: **Identità**, **Personalità**, **Comunicazione**, **Aspetto**. Only
first name and age are required. The form uses Angular Signal Forms and saves a local draft in the browser.

Rules:

- `age` is required and at least 18. The interface explains that all characters are adults.
- Each personality category accepts several descriptive entries.
- **Aspetto** shows avatar actions and starts the English tag translation when saved.
- Saving creates version 1 and an empty visual profile if none exists.

## Assisted creation

The user writes a description, such as:

> Crea un personaggio di 26 anni, appassionato di fantasy, con una personalità ironica, un carattere indipendente e un
> modo di scrivere spontaneo.

Flow:

1. `POST /characters/drafts` checks the text with the content guard and starts `character_draft`.
2. The job holds an LLM lease and generates `CharacterDraft`, which has the same fields as `CharacterCard` without
   locks and seeds. It uses the grammar and the repair attempt.
3. The result is validated. An age under 18 fails the schema, and the response explains why.
4. The result stays in `generation_jobs.result_json`. No character exists yet.
5. The Creator opens with every field filled in and editable. A note marks the fields the model invented.
6. Saving goes through the normal `POST /characters`.

If an image is being generated, the job waits, and the Creator shows the queue state.

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

Reference images are stored and shown. Version 1.0 workflows do not use them
([Image generation](image-generation.md#prepared-extensions)).

## Duplicate, archive, purge

| Action | Result |
| --- | --- |
| Duplicate | New character with a copy of the card and primary avatar, version 1, actor `duplicate`. No conversations or memories. |
| Archive | Hidden with its conversations. Nothing is deleted. |
| Purge | Preview of counts, confirmation by typing the first name, then a hard delete. See [Deletion policy](data-model.md#deletion-policy). |

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
      "examples": [
        "ok, ammetto che la mappa era al contrario. ma solo un po'",
        "se passi dal porto portami un caffè, il mio è già finito"
      ],
      "recurring_expressions": ["ma solo un po'"]
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
      "persistent_prompt_en": "1girl, adult, short messy red hair, dark green eyes, freckles, canvas jacket, brass compass necklace, ink stains on fingers",
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
- Text is checked by the content guard.
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
protocol and README screenshots:

| Name | Age | Concept | Register |
| --- | --- | --- | --- |
| Nerea Falchi | 27 | Cartographer in a shifting port city | Informal, dry irony |
| Ilario Sant'Elmo | 41 | Night-train conductor and amateur astronomer | Polite, measured, long sentences |
| Tessa Moravi | 23 | Apprentice alchemist and part-time barista | Very informal, lively, short bursts |

The first start asks whether to add them. The default answer is no, so a new installation holds no invented data.
