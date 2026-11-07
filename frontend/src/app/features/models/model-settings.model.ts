// Shapes of the model settings routes. The committed OpenAPI document does not hold these routes yet; once
// `backend/openapi.json` is regenerated, replace these interfaces with the generated types.

export type ModelKind = 'text' | 'image';
export type Tier = 'target' | 'reduced';
/** The local state of a profile's file: present with the expected size, present with another size, or absent. */
export type FileState = 'verified' | 'size_mismatch' | 'missing';
/** The prompt families a custom text file can use (docs/models.md, Custom profiles). */
export const CUSTOM_TEMPLATES = ['gemma4', 'mistral_v3_tekken', 'chatml'] as const;
export type CustomTemplate = (typeof CUSTOM_TEMPLATES)[number];

/**
 * One profile of `GET /settings/models`. `selectable`, `template` and `quality_evidence` are present on text profiles
 * only; `sha256` on custom ones only. `download_command` is null once the file is verified and for custom files.
 */
export interface ModelProfile {
  readonly id: string;
  readonly display_name: string;
  readonly file: string;
  readonly bytes: number;
  readonly license: string | null;
  readonly license_url: string | null;
  readonly min_tier: Tier;
  readonly custom: boolean;
  readonly state: FileState;
  readonly download_command: string | null;
  readonly sha256?: string;
  readonly template?: string | null;
  readonly selectable?: boolean;
  readonly quality_evidence?: string | null;
}

/** A file in a model folder that no profile names yet: it can be registered as a custom profile. */
export interface LocalFile {
  readonly kind: ModelKind;
  readonly file: string;
  readonly bytes: number;
}

export interface ModelSettings {
  readonly tier: Tier;
  readonly active: { readonly text: string; readonly image: string };
  readonly text: readonly ModelProfile[];
  readonly image: readonly ModelProfile[];
  readonly local_files: readonly LocalFile[];
  /** The launch file's KEY=VALUE pairs, or null when it is missing. */
  readonly launch: Readonly<Record<string, string>> | null;
}

/** A job as `POST /settings/models/apply` and `/custom` answer it. */
export interface QueuedJob {
  readonly id: string;
  readonly kind: string;
  readonly status: string;
  readonly queue_position: number;
}

/** A job as `GET /jobs/{id}` answers it, without the fields the screen does not read. */
export interface JobState {
  readonly id: string;
  readonly status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  readonly error_code: string | null;
  readonly error_detail: string | null;
}
