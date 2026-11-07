// The avatar tools of P03.4b in the browser through nginx and the real backend: upload with preview, crop, primary
// avatar choice, the avatar in the conversation list, and the Italian message of a refused upload.
import { deflateSync, crc32 } from 'node:zlib';

import { expect, test } from '@playwright/test';

import { uniqueName } from '../support/chats.mjs';

function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const head = Buffer.alloc(4);
  head.writeUInt32BE(data.length);
  const tail = Buffer.alloc(4);
  tail.writeUInt32BE(crc32(body));
  return Buffer.concat([head, body, tail]);
}

/** A solid-colour RGB PNG of width x height pixels. */
function png(width, height) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // RGB
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(width * 3, 0x9a)]);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

test('an avatar is uploaded, cropped and made primary, and the conversation list shows it (T-CHR-11)', async ({ page }) => {
  const name = uniqueName('Selene');
  const created = await page.request.post('/api/v1/characters', {
    data: { identity: { first_name: name, age: 30, grammatical_gender: 'femminile' } },
  });
  expect(created.status()).toBe(201);
  const id = (await created.json()).id;
  const chat = await page.request.post('/api/v1/conversations', { data: { character_id: id } });
  expect(chat.status()).toBe(201);

  await page.goto(`/characters/${id}`);
  await expect(page.getByText('Questo personaggio non ha ancora un avatar.')).toBeVisible();

  // A refused file shows the catalog text of its error code.
  await page.getByLabel("Scegli un'immagine").setInputFiles({
    name: 'finto.png',
    mimeType: 'image/png',
    buffer: Buffer.from('questo non è un\'immagine'),
  });
  await page.getByRole('button', { name: "Carica l'avatar" }).click();
  await expect(page.getByRole('alert').filter({ hasText: "Il file non è un'immagine PNG, JPEG o WebP valida." })).toBeVisible();

  // Upload with a preview: the first avatar becomes the primary one.
  await page.getByLabel("Scegli un'immagine").setInputFiles({ name: 'selene.png', mimeType: 'image/png', buffer: png(200, 100) });
  await expect(page.getByRole('img', { name: "Anteprima dell'immagine scelta" })).toBeVisible();
  await page.getByRole('button', { name: "Carica l'avatar" }).click();
  const variants = page.getByTestId('avatar-variants').getByRole('listitem');
  await expect(variants).toHaveCount(1);
  await expect(variants.first().getByText('Principale')).toBeVisible();

  // Crop it: a new variant; the original stays and keeps the primary mark.
  await page.getByRole('button', { name: 'Ritaglia la variante 1' }).click();
  await page.getByLabel('Dimensione del quadrato').fill('50');
  await page.getByRole('button', { name: 'Salva il ritaglio' }).click();
  await expect(variants).toHaveCount(2);
  const assets = await (await page.request.get(`/api/v1/characters/${id}/assets?kind=avatar`)).json();
  const cropped = assets.items.find((item) => item.crop !== null);
  expect(cropped).toBeTruthy();
  expect(cropped.is_primary).toBe(false);

  // Make the cropped variant primary; the list of chats shows it.
  await page.getByRole('button', { name: /^Rendi principale la variante/ }).click();
  await expect(page.getByText('Principale')).toHaveCount(1);
  const after = await (await page.request.get(`/api/v1/characters/${id}/assets?kind=avatar`)).json();
  expect(after.items.find((item) => item.is_primary).id).toBe(cropped.id);

  await page.goto('/chats');
  const row = page.getByRole('listitem').filter({ hasText: name });
  await expect(row.locator('img.avatar-image')).toHaveAttribute('src', `/media/assets/${cropped.id}/thumbnail`);
  await expect
    .poll(() => row.locator('img.avatar-image').evaluate((image) => image.naturalWidth))
    .toBeGreaterThan(0);
});
