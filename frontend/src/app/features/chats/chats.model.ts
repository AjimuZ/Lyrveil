// Shapes of the conversation and character routes. The committed OpenAPI document does not hold these routes yet;
// once `backend/openapi.json` is regenerated, replace these interfaces with the generated types.

/** The states a listed conversation can be in; a deleted one is never listed. */
export type ConversationStatus = 'active' | 'archived';

/** The newest finished message of a chat, for the row's preview. `text` is already cut to 140 characters. */
export interface LastMessage {
  readonly author: 'user' | 'character' | 'system';
  readonly kind: string;
  readonly text: string;
}

/** What the character is doing in a chat: producing text, drawing an image, or waiting in the queue. */
export type ConversationActivity = 'typing' | 'drawing' | 'queued';

/** One row of `GET /conversations` (docs/api.md, Conversations). */
export interface Conversation {
  readonly id: string;
  readonly character_id: string;
  readonly character_name: string;
  readonly title: string;
  readonly language: string | null;
  readonly effective_language: string;
  readonly status: 'active' | 'archived' | 'deleted';
  readonly pinned: boolean;
  readonly pinned_at: string | null;
  readonly shares_memory: boolean;
  readonly unread_count: number;
  readonly last_message_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  /** The thumbnail route of the character's primary avatar; null (or absent) while it has none. */
  readonly avatar_url?: string | null;
  /** The scenario the chat started from, null once it is deleted; `scenario_title` is the title of the chat's copy. */
  readonly scenario_id?: string | null;
  readonly scenario_title?: string | null;
  /** The finished messages of the character after the read mark; the user's own messages never count. */
  readonly last_message?: LastMessage | null;
  readonly activity?: ConversationActivity | null;
}

export interface ConversationList {
  readonly items: readonly Conversation[];
}

/** The grammatical gender values of a character card. */
export type GrammaticalGender = 'femminile' | 'maschile' | 'non_indicato';

/** One character as `GET /characters` lists it. */
export interface Character {
  readonly id: string;
  readonly first_name: string;
  readonly last_name: string | null;
  readonly full_name: string;
  readonly age: number;
  readonly grammatical_gender: GrammaticalGender;
  readonly primary_language: string;
  readonly status: 'active' | 'archived';
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CharacterList {
  readonly items: readonly Character[];
}

/** The body of `POST /characters` as the minimal form sends it. */
export interface NewCharacter {
  readonly first_name: string;
  readonly last_name: string | null;
  readonly age: number;
  readonly grammatical_gender: GrammaticalGender;
  readonly primary_language: string;
}

/** The codes the prompt knows by name (backend `LANGUAGE_NAMES`). */
export const LANGUAGE_CODES = ['it', 'en', 'fr', 'de', 'es'] as const;
export type LanguageCode = (typeof LANGUAGE_CODES)[number];
