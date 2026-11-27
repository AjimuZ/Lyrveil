// Shapes of the persona routes. The committed OpenAPI document does not hold these routes yet; once
// `backend/openapi.json` is regenerated, replace these interfaces with the generated types.
import { GrammaticalGender } from '../chats/chats.model';

export type PersonaStatus = 'active' | 'archived';

/** One persona as `GET /personas` lists it (docs/api.md, Personas). `age` null means not stated. */
export interface Persona {
  readonly id: string;
  readonly name: string;
  readonly grammatical_gender: GrammaticalGender;
  readonly age: number | null;
  readonly appearance: string;
  readonly about: string;
  readonly is_default: boolean;
  readonly status: PersonaStatus;
  /** How many conversations use the persona; above 0 it can only be archived. */
  readonly conversation_count: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface PersonaList {
  readonly items: readonly Persona[];
}

/** The body of `POST` and `PUT /personas`; a PUT replaces every field. */
export interface PersonaFields {
  readonly name: string;
  readonly grammatical_gender: GrammaticalGender;
  readonly age: number | null;
  readonly appearance: string;
  readonly about: string;
}

/** The limits the server enforces (backend `lyrveil.domain.personas`). */
export const PERSONA_LIMITS = { name: 40, appearance: 600, about: 1200, note: 600, minAge: 18, maxAge: 120 } as const;
