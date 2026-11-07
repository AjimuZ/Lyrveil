// The validation rules of the character card, one pure function per rule, mirroring backend `lyrveil.domain.card` and
// `lyrveil.services.card_rules`. Each returns a catalog problem or null, so the same rule serves the form, the unit
// tests and any other caller. The server stays the authority: these rules only refuse early what it would refuse.
import { TextKey, TextParams } from '../../core/i18n/translator';
import { SlotCategory, Vocabulary } from './card.model';

/** A refusal: the catalog key of its Italian message and the values that fill the message's placeholders. */
export interface Problem {
  readonly key: TextKey;
  readonly params?: TextParams;
}

export const MIN_AGE = 18;
export const MAX_AGE = 120;
export const MAX_NAME_LENGTH = 80;
export const MAX_TRAIT_LENGTH = 500;
export const MAX_EXAMPLES = 8;
export const MAX_EXAMPLE_USER_LENGTH = 300;
export const MAX_EXAMPLE_CHARACTER_LENGTH = 500;
export const MAX_RECURRING_EXPRESSIONS = 12;
export const MAX_AVOIDED = 8;
export const MAX_AVOIDED_LENGTH = 120;
export const MAX_SLOT_TAGS = 4;
export const MAX_EXTRA_TAGS = 6;
export const MAX_OUTFIT_TAGS = 12;
export const MAX_OUTFITS = 5;
export const MAX_OUTFIT_LABEL_LENGTH = 40;
export const MAX_OUTFIT_DESCRIPTION_LENGTH = 300;
export const MAX_FREE_TAG_WORDS = 4;
/** Larger values cannot be sent as a JSON number without losing digits; the server accepts up to 2**63 - 1. */
export const MAX_SEED = Number.MAX_SAFE_INTEGER;

/** Keys the image form reserves for the usual outfit and the outfit a scene chooses. */
export const RESERVED_OUTFIT_KEYS: readonly string[] = ['usual', 'scene'];
const OUTFIT_KEY = /^[a-z][a-z0-9_]{0,23}$/;

/** Counts code points, as the server counts characters. */
function size(text: string): number {
  return Array.from(text).length;
}

/**
 * Returns `raw` as a safe tag, or an empty string when nothing is left: the sanitizer of docs/image-generation.md.
 * Lowercase, trim, `_` to a space, drop `embedding:` with anything joined to it, keep letters, digits, spaces,
 * hyphens, apostrophes and periods, collapse spaces.
 */
export function sanitizeTag(raw: string): string {
  return raw
    .toLowerCase()
    .trim()
    .replaceAll('_', ' ')
    .replace(/\S*embedding:\S*/g, ' ')
    .replace(/[^\p{L}\p{N} \-'.]/gu, '')
    .replace(/ +/g, ' ')
    .trim();
}

export function firstNameProblem(value: string): Problem | null {
  const length = size(value.trim());
  return length >= 1 && length <= MAX_NAME_LENGTH ? null : { key: 'creator.error.firstName' };
}

export function lastNameProblem(value: string): Problem | null {
  return size(value.trim()) <= MAX_NAME_LENGTH ? null : { key: 'creator.error.lastName' };
}

/** The age arrives as typed. An empty or non-numeric text is not an integer; 17 and 121 get their own messages. */
export function ageProblem(value: string): Problem | null {
  const text = value.trim();
  if (!/^\d+$/.test(text)) {
    return { key: 'creator.error.ageInteger' };
  }
  const age = Number(text);
  if (age < MIN_AGE) {
    return { key: 'creator.error.ageMin' };
  }
  return age > MAX_AGE ? { key: 'creator.error.ageMax' } : null;
}

/** Refuses text longer than `max` characters. */
export function lengthProblem(value: string, max: number): Problem | null {
  return size(value.trim()) <= max ? null : { key: 'creator.error.tooLong', params: { max } };
}

/** Refuses a list longer than `max` entries. */
export function countProblem(count: number, max: number): Problem | null {
  return count <= max ? null : { key: 'creator.error.tooMany', params: { max } };
}

/** An example needs the character's answer; the user's message is optional. */
export function exampleProblem(user: string, character: string): Problem | null {
  if (user.trim() === '' && character.trim() === '') {
    return null;
  }
  if (character.trim() === '') {
    return { key: 'creator.error.exampleCharacter' };
  }
  return lengthProblem(user, MAX_EXAMPLE_USER_LENGTH) ?? lengthProblem(character, MAX_EXAMPLE_CHARACTER_LENGTH);
}

/** Refuses a slot value that the vocabulary does not list under `category`. An empty value means "none". */
export function slotTagProblem(vocabulary: Vocabulary, category: SlotCategory, value: string): Problem | null {
  const tag = sanitizeTag(value);
  if (value.trim() === '' || vocabulary.contains(category, tag)) {
    return null;
  }
  return { key: 'creator.error.unknownTag', params: { tag: value.trim() } };
}

/** Refuses more than `max` tags, or any tag the vocabulary does not list under `category`. */
export function slotTagsProblem(
  vocabulary: Vocabulary,
  category: SlotCategory,
  values: readonly string[],
  max = MAX_SLOT_TAGS,
): Problem | null {
  const tooMany = countProblem(values.length, max);
  if (tooMany !== null) {
    return tooMany;
  }
  for (const value of values) {
    const problem = slotTagProblem(vocabulary, category, value);
    if (problem !== null) {
      return problem;
    }
  }
  return null;
}

/** Refuses more than `max` free tags, or a tag of more than four words once sanitized. */
export function freeTagsProblem(values: readonly string[], max: number): Problem | null {
  const tooMany = countProblem(values.length, max);
  if (tooMany !== null) {
    return tooMany;
  }
  const tooLong = values.some((value) => sanitizeTag(value).split(' ').filter(Boolean).length > MAX_FREE_TAG_WORDS);
  return tooLong ? { key: 'creator.error.freeTagWords' } : null;
}

/** Refuses a reserved key, a key outside the pattern, and a key another outfit already uses. */
export function outfitKeyProblem(key: string, allKeys: readonly string[]): Problem | null {
  if (RESERVED_OUTFIT_KEYS.includes(key)) {
    return { key: 'creator.error.outfitKeyReserved' };
  }
  if (!OUTFIT_KEY.test(key)) {
    return { key: 'creator.error.outfitKeyFormat' };
  }
  return allKeys.filter((other) => other === key).length > 1 ? { key: 'creator.error.outfitKeyDuplicate' } : null;
}

export function outfitLabelProblem(label: string): Problem | null {
  const length = size(label.trim());
  return length >= 1 && length <= MAX_OUTFIT_LABEL_LENGTH ? null : { key: 'creator.error.outfitLabel' };
}

/** A `fixed` or `base_offset` policy needs a starting value; a value is a non-negative integer the browser can hold. */
export function seedProblem(policy: string, value: string): Problem | null {
  const text = value.trim();
  if (policy === 'random') {
    return null;
  }
  if (text === '') {
    return { key: 'creator.error.seedRequired' };
  }
  return /^\d+$/.test(text) && Number(text) <= MAX_SEED ? null : { key: 'creator.error.seedInvalid' };
}
