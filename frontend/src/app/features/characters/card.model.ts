// Shapes of the character card as the REST API reads and writes it (docs/character-system.md, Card schema), and the
// tag vocabulary of `GET /characters/vocabulary`. The committed OpenAPI document does not hold these routes yet; once
// `backend/openapi.json` is regenerated, replace these interfaces with the generated types.
import { GrammaticalGender } from '../chats/chats.model';

export const TRAIT_CATEGORIES = [
  'main_trait',
  'value',
  'motivation',
  'preference',
  'flaw',
  'fear',
  'habit',
  'humor',
  'temperament',
  'relational_style',
] as const;
export type TraitCategory = (typeof TRAIT_CATEGORIES)[number];

export const FORMALITIES = ['molto_informale', 'informale', 'neutra', 'formale'] as const;
export const MESSAGE_LENGTHS = ['brevissimi', 'brevi', 'medi', 'lunghi'] as const;
export const FREQUENCIES = ['raramente', 'a_volte', 'spesso'] as const;
export const EXPRESSIVENESS = ['contenuta', 'media', 'vivace'] as const;
export const ADDRESS_FORMS = ['tu', 'lei'] as const;
export const NARRATIONS = ['nessuna', 'leggera', 'libera'] as const;
export const EMOJI_USES = ['mai', 'raramente', 'a_volte', 'spesso'] as const;
export const SUBJECT_TAGS = ['1girl', '1boy', '1other'] as const;
export const SEED_POLICIES = ['random', 'fixed', 'base_offset'] as const;
export const LOCKABLE_FIELDS = [
  'hair',
  'eyes',
  'face',
  'body',
  'distinctive_marks',
  'accessories',
  'art_style',
  'clothing',
] as const;
export type LockableField = (typeof LOCKABLE_FIELDS)[number];

/** The vocabulary categories the card validates; each is one identity slot. */
export type SlotCategory =
  | 'hair_color'
  | 'hair_length'
  | 'eye_color'
  | 'skin'
  | 'hairstyle'
  | 'face'
  | 'body'
  | 'marks'
  | 'accessories'
  | 'style';

export interface Trait {
  readonly category: TraitCategory;
  readonly description: string;
  readonly locked: boolean;
}

export interface ExampleExchange {
  readonly user: string | null;
  readonly character: string;
}

export interface Outfit {
  readonly key: string;
  readonly label: string;
  readonly description: string;
  readonly tags_en: readonly string[];
}

export interface CharacterCard {
  readonly identity: {
    readonly first_name: string;
    readonly last_name: string | null;
    readonly age: number;
    readonly grammatical_gender: GrammaticalGender;
    readonly narrative_identity: string;
    readonly biography: string;
    readonly occupation: string;
    readonly interests: readonly string[];
    readonly background: string;
    readonly setting: string;
    readonly primary_language: string;
  };
  readonly traits: readonly Trait[];
  readonly communication: {
    readonly formality: string;
    readonly message_length: string;
    readonly question_frequency: string;
    readonly punctuation: string;
    readonly colloquial_style: string;
    readonly expressiveness: string;
    readonly initiative_frequency: string;
    readonly address_form: string;
    readonly narration: string;
    readonly emoji: string;
    readonly examples: readonly ExampleExchange[];
    readonly recurring_expressions: readonly string[];
    readonly avoid: readonly string[];
  };
  readonly visual: {
    readonly face: string;
    readonly hair: string;
    readonly eyes: string;
    readonly body: string;
    readonly clothing: string;
    readonly accessories: string;
    readonly distinctive_marks: string;
    readonly art_style: string;
    readonly subject_tag: string;
    readonly identity_en: {
      readonly hair_color: string | null;
      readonly hair_length: string | null;
      readonly eye_color: string | null;
      readonly skin: string | null;
      readonly hairstyle: readonly string[];
      readonly face: readonly string[];
      readonly body: readonly string[];
      readonly marks: readonly string[];
      readonly accessories: readonly string[];
      readonly style: readonly string[];
      readonly extra: readonly string[];
    };
    readonly usual_outfit_en: readonly string[];
    readonly outfits: readonly Outfit[];
    readonly persistent_negative_en: string;
    readonly locked_fields: readonly LockableField[];
    readonly seed_policy: string;
    readonly base_seed: number | null;
  };
}

/** `GET /characters/{id}`: the summary fields and the whole card. */
export interface StoredCharacter {
  readonly id: string;
  readonly full_name: string;
  readonly status: 'active' | 'archived';
  readonly is_demo: boolean;
  readonly current_version_no: number;
  readonly created_at: string;
  readonly updated_at: string;
  readonly card: CharacterCard;
}

/** One tag of the vocabulary file with the Italian words that map to it. */
export interface VocabularyEntry {
  readonly tag: string;
  readonly category: string;
  readonly aliases_it: readonly string[];
}

export interface VocabularyList {
  readonly items: readonly VocabularyEntry[];
}

/** Lowercase and without accents, so a search for "capelli ramati" or "ramàto" matches the same words. */
function fold(text: string): string {
  return text
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .trim();
}

/** The tags of the vocabulary indexed by category, with membership and Italian search. Immutable once built. */
export class Vocabulary {
  private readonly byCategory: ReadonlyMap<string, readonly VocabularyEntry[]>;

  constructor(entries: readonly VocabularyEntry[]) {
    const grouped = new Map<string, VocabularyEntry[]>();
    for (const entry of entries) {
      grouped.set(entry.category, [...(grouped.get(entry.category) ?? []), entry]);
    }
    this.byCategory = grouped;
  }

  entries(category: SlotCategory): readonly VocabularyEntry[] {
    return this.byCategory.get(category) ?? [];
  }

  /** Returns whether `tag`, exactly as written, is listed under `category`. */
  contains(category: SlotCategory, tag: string): boolean {
    return this.entries(category).some((entry) => entry.tag === tag);
  }

  /** The first Italian alias of `tag`, or the tag itself when the vocabulary does not hold it. */
  label(category: SlotCategory, tag: string): string {
    return this.entries(category).find((entry) => entry.tag === tag)?.aliases_it[0] ?? tag;
  }

  /**
   * Returns the entries of `category` whose English tag or Italian alias contains `query`, ignoring case and accents.
   * Entries where a word starts with the query come first. An empty query returns the whole category.
   */
  search(category: SlotCategory, query: string): readonly VocabularyEntry[] {
    const needle = fold(query);
    const entries = this.entries(category);
    if (needle === '') {
      return entries;
    }
    const scored: { entry: VocabularyEntry; rank: number }[] = [];
    for (const entry of entries) {
      const names = [entry.tag, ...entry.aliases_it].map(fold);
      if (names.some((name) => name.startsWith(needle) || name.split(' ').some((word) => word.startsWith(needle)))) {
        scored.push({ entry, rank: 0 });
      } else if (names.some((name) => name.includes(needle))) {
        scored.push({ entry, rank: 1 });
      }
    }
    return scored.sort((a, b) => a.rank - b.rank).map((item) => item.entry);
  }
}
