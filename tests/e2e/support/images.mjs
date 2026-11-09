// A Playwright fake of the image routes: `GET /images` in pages by an opaque cursor, `DELETE /images/{id}`, the media
// files and the character list the gallery's filter reads. It follows the contract of the real routes (newest first),
// and counts what the browser asked for, so a spec can check what was fetched and what was not.
//
// The backend half, with real rows, files and the cursor, is `test_the_gallery_pages_filters_serves_thumbnails_and_deletes`
// in `backend/tests/integration/test_image_modes.py`. No ComfyUI is involved: the media routes serve a stored file.

/** A 1x1 PNG, enough for the browser to decode a thumbnail and an original. */
const PIXEL = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

/** The metadata of image number `n` (1 is the newest); ids are unique, and the dates fall one minute apart. */
export function imageMetadata(n) {
  const id = `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
  const moment = new Date(Date.UTC(2026, 10, 7, 12, 0, 0) - n * 60_000).toISOString();
  return {
    id,
    message_id: null,
    conversation_id: null,
    character_id: 'character-1',
    purpose: 'scene',
    width: 832,
    height: 1216,
    user_request: `foto numero ${n}`,
    aspect: 'portrait',
    outfit_key: 'usual',
    prompt_positive: `positive ${n}`,
    prompt_negative: 'negative',
    seed: 1000 + n,
    steps: 28,
    cfg: 5.5,
    profile_id: 'fake-profile',
    quality_preset: 'alta',
    workflow_id: 'txt2img-hires',
    scene: {},
    created_at: moment,
    url: `/media/images/${id}/original`,
    thumbnail_url: `/media/images/${id}/thumbnail`,
  };
}

/**
 * Installs the fake image routes for `total` images, `pageSize` per page by default, and returns what the page asked
 * for: the query of every listing, how many originals and thumbnails were fetched, and the ids that were deleted.
 */
export async function fakeImages(page, { total, pageSize = 24 }) {
  let rows = Array.from({ length: total }, (_, index) => imageMetadata(index + 1));
  const seen = { listings: [], originals: 0, thumbnails: 0, deleted: [] };

  await page.route(/\/api\/v1\/images(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    seen.listings.push(url.search);
    const start = Number(url.searchParams.get('cursor') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? pageSize);
    const items = rows.slice(start, start + limit);
    const next = start + limit < rows.length ? String(start + limit) : null;
    await route.fulfill({ json: { items, next_cursor: next } });
  });
  await page.route(/\/api\/v1\/images\/[^/]+$/, async (route) => {
    const id = route.request().url().split('/').pop();
    if (route.request().method() === 'DELETE') {
      rows = rows.filter((row) => row.id !== id);
      seen.deleted.push(id);
      await route.fulfill({ status: 204 });
    } else {
      await route.fulfill({ json: rows.find((row) => row.id === id) });
    }
  });
  await page.route(/\/media\/images\/[^/]+\/(thumbnail|original)$/, async (route) => {
    if (route.request().url().endsWith('/thumbnail')) {
      seen.thumbnails += 1;
    } else {
      seen.originals += 1;
    }
    await route.fulfill({ body: PIXEL, contentType: 'image/png' });
  });
  await page.route(/\/api\/v1\/characters(\?.*)?$/, async (route) => {
    await route.fulfill({ json: { items: [{ id: 'character-1', full_name: 'Giulia', status: 'active' }] } });
  });
  return seen;
}
