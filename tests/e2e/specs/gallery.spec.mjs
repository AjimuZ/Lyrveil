// The gallery (P05.6): a character's images listed in pages as thumbnails, opened in the lightbox and deleted.
//
// Since P05.7 the Compose test project can draw images with the fake ComfyUI, but how many exist depends on the specs
// that ran before and on the mode switch's timing, so the paging could not be checked against exact counts. The browser
// plays the image routes with Playwright's routing instead, over a stateful fake that follows the
// contract of `GET /images` (newest first, an opaque cursor), `DELETE /images/{id}` and `/media/images/{id}/...`; the
// backend half, with real rows, files and the cursor, is `test_the_gallery_pages_filters_serves_thumbnails_and_deletes`
// in `backend/tests/integration/test_image_modes.py`. No ComfyUI is involved at any point: the media routes serve a
// stored file.
import { expect, test } from '@playwright/test';

const PAGE = 24;
const TOTAL = 30;
// A 1x1 PNG, enough for the browser to decode a thumbnail and an original.
const PIXEL = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

function metadata(n) {
  const id = `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
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
    created_at: `2026-11-07T10:${String(59 - n).padStart(2, '0')}:00Z`,
    url: `/media/images/${id}/original`,
    thumbnail_url: `/media/images/${id}/thumbnail`,
  };
}

/** Installs the fake image routes; returns what the page asked for. */
async function fakeImages(page) {
  let rows = Array.from({ length: TOTAL }, (_, index) => metadata(index + 1));
  const seen = { listings: [], originals: 0, thumbnails: 0, deleted: [] };

  await page.route(/\/api\/v1\/images(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    seen.listings.push(url.search);
    const start = Number(url.searchParams.get('cursor') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? PAGE);
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

test('the gallery lists thumbnails in pages, opens the lightbox and deletes an image', async ({ page }) => {
  const seen = await fakeImages(page);
  await page.goto('/gallery');

  await expect(page.getByRole('heading', { level: 1, name: 'Galleria' })).toBeVisible();
  const grid = page.getByRole('list', { name: 'Immagini' });
  await expect(grid.getByRole('listitem')).toHaveCount(PAGE);
  expect(seen.listings[0]).toContain(`limit=${PAGE}`);

  // Lists use thumbnails: the grid asked for no original, and only lazily for the thumbnails.
  expect(seen.originals).toBe(0);
  await expect(grid.locator('img').first()).toHaveAttribute('src', /\/media\/images\/[^/]+\/thumbnail$/);

  // The next page is requested by its cursor and appended.
  await page.getByRole('button', { name: 'Carica altre' }).click();
  await expect(grid.getByRole('listitem')).toHaveCount(TOTAL);
  expect(seen.listings[1]).toContain('cursor=');
  await expect(page.getByRole('button', { name: 'Carica altre' })).toHaveCount(0);

  // The lightbox shows the original and its metadata.
  await grid.getByRole('button', { name: /foto numero 3$/ }).click();
  const lightbox = page.getByRole('dialog', { name: 'Immagine ingrandita' });
  await expect(lightbox).toBeVisible();
  await expect(lightbox.getByTestId('gallery-seed')).toHaveText('1003');
  await expect(lightbox.getByText('positive 3')).toBeVisible();
  await expect(lightbox.locator('img')).toHaveAttribute('src', /\/original$/);
  await expect.poll(() => seen.originals).toBeGreaterThan(0);

  // It closes with Escape and opens again.
  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
  await grid.getByRole('button', { name: /foto numero 3$/ }).click();

  // Delete asks for a confirmation, then removes the image from the grid.
  await lightbox.getByRole('button', { name: 'Elimina' }).click();
  await expect(lightbox.getByText('Eliminare questa immagine per sempre?').first()).toBeVisible();
  await lightbox.getByRole('button', { name: 'Annulla' }).click();
  expect(seen.deleted).toHaveLength(0);
  await lightbox.getByRole('button', { name: 'Elimina' }).click();
  await lightbox.getByRole('button', { name: 'Sì, elimina' }).click();
  await expect(lightbox).toBeHidden();
  await expect(grid.getByRole('listitem')).toHaveCount(TOTAL - 1);
  await expect(grid.getByRole('button', { name: /foto numero 3$/ })).toHaveCount(0);
  expect(seen.deleted).toHaveLength(1);
});
