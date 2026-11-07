// Shapes of the scenario routes. The committed OpenAPI document does not hold these routes yet; once
// `backend/openapi.json` is regenerated, replace these interfaces with the generated types.

export type ScenarioStatus = 'active' | 'archived';

/** One scenario as `GET /scenarios` lists it (docs/api.md, Scenarios). `character_id` null means any character. */
export interface Scenario {
  readonly id: string;
  readonly title: string;
  readonly description: string;
  readonly setting: string;
  readonly opening_message: string;
  readonly character_id: string | null;
  readonly language: string | null;
  readonly status: ScenarioStatus;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ScenarioList {
  readonly items: readonly Scenario[];
}

/** The body of `POST` and `PUT /scenarios`; a PUT replaces every field. */
export interface ScenarioFields {
  readonly title: string;
  readonly description: string;
  readonly setting: string;
  readonly opening_message: string;
  readonly character_id: string | null;
  readonly language: string | null;
}

/** The limits the server enforces (backend `lyrveil.domain.scenarios`). */
export const SCENARIO_LIMITS = { title: 200, description: 2000, setting: 2000, openingMessage: 2000 } as const;
