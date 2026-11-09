// The gallery (P05.6): a character's images listed in pages as thumbnails, opened in the lightbox and deleted.
//
// Since P05.7 the Compose test project can draw images with the fake ComfyUI, but how many exist depends on the specs
// that ran before and on the mode switch's timing, so the paging could not be checked against exact counts. The browser
// plays the image routes with Playwright's routing instead, over a stateful fake (`support/images.mjs`) that follows the
// contract of `GET /images` (newest first, an opaque cursor), `DELETE /images/{id}` and `/media/images/{id}/...`; the
// backend half, with real rows, files and the cursor, is `test_the_gallery_pages_filters_serves_thumbnails_and_deletes`
// in `backend/tests/integration/test_image_modes.py`. No ComfyUI is involved at any point: the media routes serve a
// stored file. A gallery of 1,000 images is in `performance.spec.mjs`.
import { expect, test } from '@playwright/test';

import { fakeImages } from '../support/images.mjs';

const PAGE = 24;
const TOTAL = 30;

test('the gallery lists thumbnails in pages, opens the lightbox and deletes an image', async ({ page }) => {
  const seen = await fakeImages(page, { total: TOTAL, pageSize: PAGE });
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
