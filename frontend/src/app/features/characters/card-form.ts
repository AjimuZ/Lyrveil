// The form model of the Character Creator: its shape, the conversion to and from the API card, and the validation
// schema of Signal Forms. The model holds what the controls edit (text, rows of objects, tag arrays); the card is what
// the server stores. Conversion keeps them apart so that a half-typed value never has to be a valid card.
import { applyEach, schema, SchemaPath, validate, ValidationError } from '@angular/forms/signals';

import { Translator } from '../../core/i18n/translator';
import { GrammaticalGender } from '../chats/chats.model';
import {
  ageProblem,
  countProblem,
  exampleProblem,
  firstNameProblem,
  freeTagsProblem,
  lastNameProblem,
  lengthProblem,
  MAX_AVOIDED,
  MAX_AVOIDED_LENGTH,
  MAX_EXAMPLES,
  MAX_EXTRA_TAGS,
  MAX_OUTFIT_DESCRIPTION_LENGTH,
  MAX_OUTFIT_TAGS,
  MAX_OUTFITS,
  MAX_RECURRING_EXPRESSIONS,
  MAX_TRAIT_LENGTH,
  outfitKeyProblem,
  outfitLabelProblem,
  Problem,
  sanitizeTag,
  seedProblem,
  slotTagProblem,
  slotTagsProblem,
} from './card-rules';
import {
  CharacterCard,
  ExampleExchange,
  LockableField,
  SlotCategory,
  TraitCategory,
  Vocabulary,
} from './card.model';

export interface TextRow {
  text: string;
}

export interface TraitRow {
  category: TraitCategory;
  description: string;
  locked: boolean;
}

export interface ExampleRow {
  user: string;
  character: string;
}

export interface OutfitRow {
  key: string;
  label: string;
  description: string;
  tags: string[];
}

export interface CreatorModel {
  identity: {
    firstName: string;
    lastName: string;
    age: string;
    gender: GrammaticalGender;
    primaryLanguage: string;
    narrativeIdentity: string;
    occupation: string;
    biography: string;
    background: string;
    setting: string;
    interests: TextRow[];
  };
  traits: TraitRow[];
  communication: {
    formality: string;
    messageLength: string;
    questionFrequency: string;
    punctuation: string;
    colloquialStyle: string;
    expressiveness: string;
    initiativeFrequency: string;
    addressForm: string;
    narration: string;
    emoji: string;
    examples: ExampleRow[];
    recurringExpressions: TextRow[];
    avoid: TextRow[];
  };
  visual: {
    face: string;
    hair: string;
    eyes: string;
    body: string;
    clothing: string;
    accessories: string;
    distinctiveMarks: string;
    artStyle: string;
    subjectTag: string;
    hairColor: string;
    hairLength: string;
    eyeColor: string;
    skin: string;
    hairstyle: string[];
    faceTags: string[];
    bodyTags: string[];
    marks: string[];
    accessoryTags: string[];
    style: string[];
    extra: string[];
    usualOutfit: string[];
    outfits: OutfitRow[];
    persistentNegative: string;
    lockedFields: LockableField[];
    seedPolicy: string;
    baseSeed: string;
  };
}

/** The model of a new character: the defaults of the card, with the three required identity fields empty. */
export function emptyModel(): CreatorModel {
  return {
    identity: {
      firstName: '',
      lastName: '',
      age: '',
      gender: 'non_indicato',
      primaryLanguage: 'it',
      narrativeIdentity: '',
      occupation: '',
      biography: '',
      background: '',
      setting: '',
      interests: [],
    },
    traits: [],
    communication: {
      formality: 'informale',
      messageLength: 'brevi',
      questionFrequency: 'a_volte',
      punctuation: '',
      colloquialStyle: '',
      expressiveness: 'media',
      initiativeFrequency: 'a_volte',
      addressForm: 'tu',
      narration: 'nessuna',
      emoji: 'raramente',
      examples: [],
      recurringExpressions: [],
      avoid: [],
    },
    visual: {
      face: '',
      hair: '',
      eyes: '',
      body: '',
      clothing: '',
      accessories: '',
      distinctiveMarks: '',
      artStyle: '',
      subjectTag: '1other',
      hairColor: '',
      hairLength: '',
      eyeColor: '',
      skin: '',
      hairstyle: [],
      faceTags: [],
      bodyTags: [],
      marks: [],
      accessoryTags: [],
      style: [],
      extra: [],
      usualOutfit: [],
      outfits: [],
      persistentNegative: '',
      lockedFields: [],
      seedPolicy: 'random',
      baseSeed: '',
    },
  };
}

const rows = (values: readonly string[]): TextRow[] => values.map((text) => ({ text }));
const texts = (values: readonly TextRow[]): string[] => values.map((row) => row.text.trim()).filter(Boolean);
const tags = (values: readonly string[]): string[] => [...values];

/** Returns the model that edits `card`. */
export function toModel(card: CharacterCard): CreatorModel {
  const { identity, communication, visual } = card;
  const slots = visual.identity_en;
  return {
    identity: {
      firstName: identity.first_name,
      lastName: identity.last_name ?? '',
      age: String(identity.age),
      gender: identity.grammatical_gender,
      primaryLanguage: identity.primary_language,
      narrativeIdentity: identity.narrative_identity,
      occupation: identity.occupation,
      biography: identity.biography,
      background: identity.background,
      setting: identity.setting,
      interests: rows(identity.interests),
    },
    traits: card.traits.map((trait) => ({ ...trait })),
    communication: {
      formality: communication.formality,
      messageLength: communication.message_length,
      questionFrequency: communication.question_frequency,
      punctuation: communication.punctuation,
      colloquialStyle: communication.colloquial_style,
      expressiveness: communication.expressiveness,
      initiativeFrequency: communication.initiative_frequency,
      addressForm: communication.address_form,
      narration: communication.narration,
      emoji: communication.emoji,
      examples: communication.examples.map((example) => ({ user: example.user ?? '', character: example.character })),
      recurringExpressions: rows(communication.recurring_expressions),
      avoid: rows(communication.avoid),
    },
    visual: {
      face: visual.face,
      hair: visual.hair,
      eyes: visual.eyes,
      body: visual.body,
      clothing: visual.clothing,
      accessories: visual.accessories,
      distinctiveMarks: visual.distinctive_marks,
      artStyle: visual.art_style,
      subjectTag: visual.subject_tag,
      hairColor: slots.hair_color ?? '',
      hairLength: slots.hair_length ?? '',
      eyeColor: slots.eye_color ?? '',
      skin: slots.skin ?? '',
      hairstyle: tags(slots.hairstyle),
      faceTags: tags(slots.face),
      bodyTags: tags(slots.body),
      marks: tags(slots.marks),
      accessoryTags: tags(slots.accessories),
      style: tags(slots.style),
      extra: tags(slots.extra),
      usualOutfit: tags(visual.usual_outfit_en),
      outfits: visual.outfits.map((outfit) => ({
        key: outfit.key,
        label: outfit.label,
        description: outfit.description,
        tags: tags(outfit.tags_en),
      })),
      persistentNegative: visual.persistent_negative_en,
      lockedFields: [...visual.locked_fields],
      seedPolicy: visual.seed_policy,
      baseSeed: visual.base_seed === null ? '' : String(visual.base_seed),
    },
  };
}

/**
 * Returns the card body for `POST` and `PUT /characters`. Text is trimmed and blank rows are left out, as the server
 * does. A starting seed is sent only with a policy that uses it. Call it on a model that passed the schema: a
 * non-numeric age becomes NaN, which the server refuses.
 */
export function toCard(model: CreatorModel): Record<string, unknown> {
  const { identity, communication, visual } = model;
  const text = (value: string): string => value.trim();
  return {
    identity: {
      first_name: text(identity.firstName),
      last_name: text(identity.lastName) || null,
      age: Number(identity.age.trim()),
      grammatical_gender: identity.gender,
      primary_language: identity.primaryLanguage,
      narrative_identity: text(identity.narrativeIdentity),
      occupation: text(identity.occupation),
      biography: text(identity.biography),
      background: text(identity.background),
      setting: text(identity.setting),
      interests: texts(identity.interests),
    },
    traits: model.traits
      .filter((trait) => trait.description.trim() !== '')
      .map((trait) => ({ category: trait.category, description: text(trait.description), locked: trait.locked })),
    communication: {
      formality: communication.formality,
      message_length: communication.messageLength,
      question_frequency: communication.questionFrequency,
      punctuation: text(communication.punctuation),
      colloquial_style: text(communication.colloquialStyle),
      expressiveness: communication.expressiveness,
      initiative_frequency: communication.initiativeFrequency,
      address_form: communication.addressForm,
      narration: communication.narration,
      emoji: communication.emoji,
      examples: communication.examples
        .filter((example) => example.user.trim() !== '' || example.character.trim() !== '')
        .map(
          (example): ExampleExchange => ({ user: text(example.user) || null, character: text(example.character) }),
        ),
      recurring_expressions: texts(communication.recurringExpressions),
      avoid: texts(communication.avoid),
    },
    visual: {
      face: text(visual.face),
      hair: text(visual.hair),
      eyes: text(visual.eyes),
      body: text(visual.body),
      clothing: text(visual.clothing),
      accessories: text(visual.accessories),
      distinctive_marks: text(visual.distinctiveMarks),
      art_style: text(visual.artStyle),
      subject_tag: visual.subjectTag,
      identity_en: {
        hair_color: visual.hairColor || null,
        hair_length: visual.hairLength || null,
        eye_color: visual.eyeColor || null,
        skin: visual.skin || null,
        hairstyle: visual.hairstyle,
        face: visual.faceTags,
        body: visual.bodyTags,
        marks: visual.marks,
        accessories: visual.accessoryTags,
        style: visual.style,
        extra: visual.extra,
      },
      usual_outfit_en: visual.usualOutfit,
      outfits: visual.outfits.map((outfit) => ({
        key: outfit.key,
        label: text(outfit.label),
        description: text(outfit.description),
        tags_en: outfit.tags,
      })),
      persistent_negative_en: text(visual.persistentNegative),
      locked_fields: visual.lockedFields,
      seed_policy: visual.seedPolicy,
      base_seed: visual.seedPolicy === 'random' ? null : Number(visual.baseSeed.trim()),
    },
  };
}

/** The English identity, read-only, in the order of ADR 0035: hair, eyes, skin, face, body, marks, accessories, extra. */
export function renderIdentity(visual: CreatorModel['visual']): string {
  const parts = [
    visual.hairColor,
    visual.hairLength,
    ...visual.hairstyle,
    visual.eyeColor,
    visual.skin,
    ...visual.faceTags,
    ...visual.bodyTags,
    ...visual.marks,
    ...visual.accessoryTags,
    ...visual.extra,
  ];
  return parts
    .map((part) => sanitizeTag(part))
    .filter(Boolean)
    .join(', ');
}

type Fail = ValidationError.WithoutFieldTree | null;

/**
 * Returns the schema of the Creator form. Every rule of `card-rules` is attached to its field, and a refusal carries
 * the Italian message from the catalog, so a control only has to show `errors()[0].message`. `vocabulary` is read
 * inside the rules, so a vocabulary that arrives after the form revalidates the slots.
 */
export function creatorSchema(vocabulary: () => Vocabulary, translator: Translator) {
  const refuse = (problem: Problem | null): Fail =>
    problem === null ? null : { kind: 'card', message: translator.t(problem.key, problem.params) };

  return schema<CreatorModel>((path) => {
    validate(path.identity.firstName, (ctx) => refuse(firstNameProblem(ctx.value())));
    validate(path.identity.lastName, (ctx) => refuse(lastNameProblem(ctx.value())));
    validate(path.identity.age, (ctx) => refuse(ageProblem(ctx.value())));
    for (const field of ['narrativeIdentity', 'occupation', 'biography', 'background', 'setting'] as const) {
      validate(path.identity[field], (ctx) => refuse(lengthProblem(ctx.value(), 4000)));
    }
    applyEach(path.identity.interests, (item) => {
      validate(item.text, (ctx) => refuse(lengthProblem(ctx.value(), MAX_AVOIDED_LENGTH)));
    });

    applyEach(path.traits, (trait) => {
      validate(trait.description, (ctx) => refuse(lengthProblem(ctx.value(), MAX_TRAIT_LENGTH)));
    });

    applyEach(path.communication.examples, (example) => {
      validate(example.character, (ctx) => refuse(exampleProblem(ctx.valueOf(example.user), ctx.value())));
    });
    validate(path.communication.examples, (ctx) => refuse(countProblem(ctx.value().length, MAX_EXAMPLES)));
    validate(path.communication.recurringExpressions, (ctx) =>
      refuse(countProblem(ctx.value().length, MAX_RECURRING_EXPRESSIONS)),
    );
    applyEach(path.communication.recurringExpressions, (item) => {
      validate(item.text, (ctx) => refuse(lengthProblem(ctx.value(), 200)));
    });
    validate(path.communication.avoid, (ctx) => refuse(countProblem(ctx.value().length, MAX_AVOIDED)));
    applyEach(path.communication.avoid, (item) => {
      validate(item.text, (ctx) => refuse(lengthProblem(ctx.value(), MAX_AVOIDED_LENGTH)));
    });

    const visual = path.visual;
    const slot = (field: SchemaPath<string>, category: SlotCategory): void =>
      validate(field, (ctx) => refuse(slotTagProblem(vocabulary(), category, ctx.value())));
    slot(visual.hairColor, 'hair_color');
    slot(visual.hairLength, 'hair_length');
    slot(visual.eyeColor, 'eye_color');
    slot(visual.skin, 'skin');
    const slots = (field: SchemaPath<string[]>, category: SlotCategory): void =>
      validate(field, (ctx) => refuse(slotTagsProblem(vocabulary(), category, ctx.value())));
    slots(visual.hairstyle, 'hairstyle');
    slots(visual.faceTags, 'face');
    slots(visual.bodyTags, 'body');
    slots(visual.marks, 'marks');
    slots(visual.accessoryTags, 'accessories');
    slots(visual.style, 'style');
    validate(visual.extra, (ctx) => refuse(freeTagsProblem(ctx.value(), MAX_EXTRA_TAGS)));
    validate(visual.usualOutfit, (ctx) => refuse(freeTagsProblem(ctx.value(), MAX_OUTFIT_TAGS)));
    validate(visual.outfits, (ctx) => refuse(countProblem(ctx.value().length, MAX_OUTFITS)));
    applyEach(visual.outfits, (outfit) => {
      validate(outfit.key, (ctx) =>
        refuse(
          outfitKeyProblem(
            ctx.value(),
            ctx.valueOf(visual.outfits).map((other) => other.key),
          ),
        ),
      );
      validate(outfit.label, (ctx) => refuse(outfitLabelProblem(ctx.value())));
      validate(outfit.description, (ctx) => refuse(lengthProblem(ctx.value(), MAX_OUTFIT_DESCRIPTION_LENGTH)));
      validate(outfit.tags, (ctx) => refuse(freeTagsProblem(ctx.value(), MAX_OUTFIT_TAGS)));
    });
    validate(visual.baseSeed, (ctx) => refuse(seedProblem(ctx.valueOf(visual.seedPolicy), ctx.value())));
  });
}
