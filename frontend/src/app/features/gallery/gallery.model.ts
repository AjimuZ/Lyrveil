// Shapes of the image routes of docs/api.md (Images) that the gallery uses. The committed OpenAPI document does not
// hold these routes yet; replace these interfaces with the generated types once `backend/openapi.json` is regenerated.

export const IMAGE_PURPOSES = ['chat', 'avatar', 'profile', 'scene', 'variant'] as const;
export type ImagePurpose = (typeof IMAGE_PURPOSES)[number];

/** One image as `GET /images` and `GET /images/{id}` show it. `url` and `thumbnail_url` are `/media` routes. */
export interface GalleryImage {
  readonly id: string;
  readonly message_id: string | null;
  readonly conversation_id: string | null;
  readonly character_id: string;
  readonly purpose: ImagePurpose;
  readonly width: number;
  readonly height: number;
  readonly user_request: string;
  readonly aspect: string;
  readonly outfit_key: string;
  readonly prompt_positive: string;
  readonly prompt_negative: string;
  readonly seed: number;
  readonly steps: number;
  readonly cfg: number;
  readonly profile_id: string;
  readonly quality_preset: string;
  readonly workflow_id: string;
  readonly scene: Readonly<Record<string, unknown>>;
  readonly created_at: string;
  readonly url: string;
  readonly thumbnail_url: string;
}

/** One page of the gallery; `next_cursor` is the `cursor` of the next page, null on the last. */
export interface GalleryPage {
  readonly items: readonly GalleryImage[];
  readonly next_cursor: string | null;
}

/** What the filters of the screen select; an empty string means no filter. */
export interface GalleryFilters {
  readonly characterId: string;
  readonly purpose: ImagePurpose | '';
}

export const NO_FILTERS: GalleryFilters = { characterId: '', purpose: '' };
