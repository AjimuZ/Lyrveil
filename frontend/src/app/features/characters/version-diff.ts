// How the profile shows a comparison of two card versions: the Italian label of a field path and a readable value.
import { TextKey } from '../../core/i18n/translator';

/** Catalog text of every card field path the diff can name. A list (traits, examples, outfits) is one path. */
const FIELD_LABELS: Readonly<Record<string, TextKey>> = {
  'identity.first_name': 'creator.firstName',
  'identity.last_name': 'creator.lastName',
  'identity.age': 'creator.age',
  'identity.grammatical_gender': 'creator.gender',
  'identity.narrative_identity': 'creator.narrativeIdentity',
  'identity.biography': 'creator.biography',
  'identity.occupation': 'creator.occupation',
  'identity.interests': 'creator.interests',
  'identity.background': 'creator.background',
  'identity.setting': 'creator.setting',
  'identity.primary_language': 'creator.language',
  traits: 'profile.personality',
  'communication.formality': 'creator.formality',
  'communication.message_length': 'creator.messageLength',
  'communication.question_frequency': 'creator.questionFrequency',
  'communication.punctuation': 'creator.punctuation',
  'communication.colloquial_style': 'creator.colloquialStyle',
  'communication.expressiveness': 'creator.expressiveness',
  'communication.initiative_frequency': 'creator.initiativeFrequency',
  'communication.address_form': 'creator.addressForm',
  'communication.narration': 'creator.narration',
  'communication.emoji': 'creator.emoji',
  'communication.examples': 'creator.examples',
  'communication.recurring_expressions': 'creator.expressions',
  'communication.avoid': 'creator.avoid',
  'visual.face': 'creator.face',
  'visual.hair': 'creator.hair',
  'visual.eyes': 'creator.eyes',
  'visual.body': 'creator.body',
  'visual.clothing': 'creator.clothing',
  'visual.accessories': 'creator.accessories',
  'visual.distinctive_marks': 'creator.distinctiveMarks',
  'visual.art_style': 'creator.artStyle',
  'visual.subject_tag': 'creator.subject',
  'visual.identity_en.hair_color': 'creator.hairColor',
  'visual.identity_en.hair_length': 'creator.hairLength',
  'visual.identity_en.eye_color': 'creator.eyeColor',
  'visual.identity_en.skin': 'creator.skin',
  'visual.identity_en.hairstyle': 'creator.hairstyle',
  'visual.identity_en.face': 'creator.faceTags',
  'visual.identity_en.body': 'creator.bodyTags',
  'visual.identity_en.marks': 'creator.marks',
  'visual.identity_en.accessories': 'creator.accessoryTags',
  'visual.identity_en.style': 'creator.style',
  'visual.identity_en.extra': 'creator.extra',
  'visual.usual_outfit_en': 'creator.usualOutfit',
  'visual.outfits': 'creator.wardrobe',
  'visual.persistent_negative_en': 'creator.negative',
  'visual.locked_fields': 'profile.lockedFields',
  'visual.seed_policy': 'creator.seedPolicy',
  'visual.base_seed': 'creator.baseSeed',
};

/** Returns the catalog key that names the card field at `path`, or null for a path this screen does not know. */
export function fieldLabelKey(path: string): TextKey | null {
  return FIELD_LABELS[path] ?? null;
}

/**
 * Returns `value` as a short readable text. An empty value (null, an empty string or list) is `—`, a boolean is
 * `sì` or `no`, a list joins its items with `; ` and an object lists `key: value` pairs.
 */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  if (typeof value === 'boolean') {
    return value ? 'sì' : 'no';
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? '—' : (value as unknown[]).map(formatValue).join('; ');
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, inner]) => `${key}: ${formatValue(inner)}`)
      .join(', ');
  }
  return String(value as string | number);
}
