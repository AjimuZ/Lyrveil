// Shapes of the lore routes and the pure rules the editors share: the limits, the key lists and the field a refusal names.
// The committed OpenAPI document does not hold these routes yet; once `backend/openapi.json` is regenerated, replace the
// interfaces with the generated types.

export type BookStatus = 'active' | 'archived';
export type BookKind = 'world' | 'character';

/** A world or a character's own book as `GET /lore/books/{id}` answers (docs/api.md, Lore). */
export interface LoreBook {
  readonly id: string;
  readonly kind: BookKind;
  /** The owner of an own book; null for a world. */
  readonly character_id: string | null;
  readonly name: string;
  readonly description: string;
  readonly scan_depth: number;
  /** Null means the block's default budget. */
  readonly token_budget: number | null;
  readonly status: BookStatus;
  readonly entry_count: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface LoreBookList {
  readonly items: readonly LoreBook[];
}

/** The body of `POST /lore/worlds` and `PUT /lore/books/{id}`; a PUT replaces every field. */
export interface BookFields {
  readonly name: string;
  readonly description: string;
  readonly scan_depth: number;
  readonly token_budget: number | null;
}

/** One entry. `never_inserted` is true when its line is over its block's maximum, so no prompt ever reads it. */
export interface LoreEntry {
  readonly id: string;
  readonly book_id: string;
  readonly title: string;
  readonly content: string;
  readonly keys: readonly string[];
  readonly secondary_keys: readonly string[];
  readonly exclude_keys: readonly string[];
  readonly case_sensitive: boolean;
  readonly constant: boolean;
  readonly enabled: boolean;
  readonly priority: number;
  readonly insertion_order: number;
  readonly note: string;
  readonly never_inserted: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface LoreEntryList {
  readonly items: readonly LoreEntry[];
}

/** The body of `POST /lore/books/{id}/entries` and `PUT /lore/entries/{id}`; a PUT replaces every field. */
export interface EntryFields {
  readonly title: string;
  readonly content: string;
  readonly keys: readonly string[];
  readonly secondary_keys: readonly string[];
  readonly exclude_keys: readonly string[];
  readonly case_sensitive: boolean;
  readonly constant: boolean;
  readonly enabled: boolean;
  readonly priority: number;
  readonly insertion_order: number;
  readonly note: string;
}

/** The limits the server enforces (backend `lyrveil.domain.lore`). */
export const LORE_LIMITS = {
  name: 120,
  description: 2000,
  minScanDepth: 1,
  maxScanDepth: 20,
  defaultScanDepth: 4,
  minBudget: 50,
  maxBudget: 900,
  title: 80,
  content: 8000,
  keysPerList: 16,
  keyChars: 60,
  note: 500,
  orderMagnitude: 1_000_000,
  defaultPriority: 0,
  defaultInsertionOrder: 100,
} as const;

/** Fields of the book form a refusal can name. */
export type BookField = 'name' | 'description' | 'scan_depth' | 'token_budget';

/** Fields of the entry form a refusal can name. */
export type EntryField =
  | 'title'
  | 'content'
  | 'keys'
  | 'secondary_keys'
  | 'exclude_keys'
  | 'priority'
  | 'insertion_order'
  | 'note'
  | 'case_sensitive'
  | 'constant'
  | 'enabled';

/** The labels the server's Italian details put between «», and the field each one names. */
const BOOK_LABELS: readonly (readonly [string, BookField])[] = [
  ['nome', 'name'],
  ['descrizione', 'description'],
  ['messaggi da leggere', 'scan_depth'],
  ['budget di token', 'token_budget'],
];

const ENTRY_LABELS: readonly (readonly [string, EntryField])[] = [
  ['titolo', 'title'],
  ['contenuto', 'content'],
  ['parole chiave', 'keys'],
  ['parole chiave secondarie', 'secondary_keys'],
  ['parole chiave da escludere', 'exclude_keys'],
  ['priorità', 'priority'],
  ['ordine', 'insertion_order'],
  ['nota', 'note'],
  ['maiuscole e minuscole', 'case_sensitive'],
  ['sempre attiva', 'constant'],
  ['attiva', 'enabled'],
];

const NO_STEM = /^La parola chiave «(.+)» non ha parole utili/;

function labelled<F extends string>(detail: string, labels: readonly (readonly [string, F])[]): F | null {
  return labels.find(([label]) => detail.includes(`«${label}»`))?.[1] ?? null;
}

/**
 * Returns the field of the book form that a refusal's Italian `detail` names, or null when it names none. The server
 * words each refusal as «nome», «descrizione», «messaggi da leggere» or «budget di token» (lore_rules.py), so the form
 * can show the text next to the field.
 */
export function bookFieldOf(detail: string | null): BookField | null {
  return detail === null ? null : labelled(detail, BOOK_LABELS);
}

/**
 * Returns the field of the entry form that a refusal's Italian `detail` names, or null. A key with no useful word is
 * worded without a label (`La parola chiave «lui» non ha parole utili…`), so it is placed in the first list of `keys`
 * that holds it.
 */
export function entryFieldOf(
  detail: string | null,
  lists: Readonly<Record<'keys' | 'secondary_keys' | 'exclude_keys', readonly string[]>>,
): EntryField | null {
  if (detail === null) {
    return null;
  }
  const named = labelled(detail, ENTRY_LABELS);
  if (named !== null) {
    return named;
  }
  const key = NO_STEM.exec(detail)?.[1];
  if (key === undefined) {
    return null;
  }
  for (const list of ['keys', 'secondary_keys', 'exclude_keys'] as const) {
    if (lists[list].includes(key)) {
      return list;
    }
  }
  return null;
}

/** Splits a typed list on commas into trimmed keys, without blanks or repeats, as the server stores them. */
export function parseKeys(text: string): string[] {
  return [...new Set(text.split(',').map((item) => item.trim()).filter((item) => item !== ''))];
}

/** Joins keys for the text box, the inverse of `parseKeys`. */
export function formatKeys(keys: readonly string[]): string {
  return keys.join(', ');
}

/** Whether `text` is a whole number (a leading minus allowed) with a magnitude the server accepts. */
export function isOrderNumber(text: string): boolean {
  const value = text.trim();
  return /^-?\d+$/.test(value) && Math.abs(Number(value)) <= LORE_LIMITS.orderMagnitude;
}
