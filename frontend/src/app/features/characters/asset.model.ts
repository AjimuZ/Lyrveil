// Shapes of the asset routes of docs/api.md (Assets). The committed OpenAPI document does not hold these routes yet;
// replace these interfaces with the generated types once `backend/openapi.json` is regenerated.

/** One character asset as `GET /characters/{id}/assets` lists it. `url` and `thumbnail_url` are `/media` routes. */
export interface CharacterAsset {
  readonly id: string;
  readonly character_id: string;
  readonly kind: string;
  readonly source: string;
  readonly media_type: string;
  readonly width: number;
  readonly height: number;
  readonly byte_size: number;
  readonly is_primary: boolean;
  readonly created_at: string;
  readonly url: string;
  readonly thumbnail_url: string;
}

export interface AssetList {
  readonly items: readonly CharacterAsset[];
}

/** A square of the source image in pixels, as `POST .../crop` takes it. */
export interface CropRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/**
 * Returns the square a crop control describes: `sizePercent` of the shorter side (clamped to 1..100), placed by
 * `xPercent` and `yPercent` (0 = left or top edge, 100 = right or bottom edge) within the room the square leaves.
 * The result always lies inside a `width` x `height` image.
 */
export function squareCrop(width: number, height: number, sizePercent: number, xPercent: number, yPercent: number): CropRect {
  const shorter = Math.max(1, Math.min(width, height));
  const side = Math.max(1, Math.min(shorter, Math.round((shorter * clamp(sizePercent, 1, 100)) / 100)));
  const x = Math.round(((width - side) * clamp(xPercent, 0, 100)) / 100);
  const y = Math.round(((height - side) * clamp(yPercent, 0, 100)) / 100);
  return { x, y, width: side, height: side };
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}
