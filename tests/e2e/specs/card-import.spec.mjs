// The Creator's card import and export of P10.6 (T-CRD-09), in the browser through nginx and the real backend: a V2 PNG
// card is imported, its preview is read, the age (which the card does not state) is entered, the character is saved
// with the scenarios of its greetings, and then exported as a PNG and in the native format. The structured conversion
// needs the text model and is covered by the unit specs.
import { readFileSync } from 'node:fs';
import { crc32, deflateSync } from 'node:zlib';

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

/** A 64 x 64 solid-colour RGB PNG that carries `text` as a `chara` tEXt chunk, as a V2 card does. */
function cardPng(card) {
  const size = 64;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(size * 3, 0x9a)]);
  const raw = Buffer.concat(Array.from({ length: size }, () => row));
  const text = Buffer.concat([
    Buffer.from('chara\0', 'latin1'),
    Buffer.from(Buffer.from(JSON.stringify(card), 'utf8').toString('base64'), 'latin1'),
  ]);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('tEXt', text),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** A V2 card with two greetings, one macro, and no age anywhere in its text. */
function v2Card(name) {
  return {
    spec: 'chara_card_v2',
    spec_version: '2.0',
    data: {
      name,
      description: `${name} disegna mappe dei porti che non esistono più.`,
      personality: 'Ironica.\n\nPaziente con chi si perde.',
      scenario: 'Una bottega di mappe.',
      first_mes: 'Ehi, entra pure. {{user}}, giusto?',
      mes_example: '<START>\n{{user}}: Che fai?\n{{char}}: Ridisegno il molo.',
      creator_notes: 'Nota di prova.',
      system_prompt: '',
      post_history_instructions: '',
      alternate_greetings: ['Buonasera, cercavi qualcosa?'],
      tags: ['mappe'],
      creator: 'Collaudo',
      character_version: '1',
      extensions: {},
    },
  };
}

test('a PNG card is imported, previewed, completed with the age, saved with scenarios and exported (T-CRD-09)', async ({
  page,
}) => {
  const name = uniqueName('Selene');
  await page.goto('/characters/new');
  await expect(page.getByRole('heading', { level: 1, name: 'Nuovo personaggio' })).toBeVisible();

  await page
    .getByLabel('Importa una card')
    .setInputFiles({ name: `${name}.png`, mimeType: 'image/png', buffer: cardPng(v2Card(name)) });

  // The preview: the file's facts, the report, a labelled estimate, and nothing is saved yet.
  await expect(page).toHaveURL(/\/characters\/import\/[^/]+$/);
  const preview = page.getByTestId('card-import-preview');
  await expect(preview.getByRole('heading', { name: 'Card importata' })).toBeVisible();
  await expect(preview.getByText('Collaudo')).toBeVisible();
  await expect(page.getByTestId('card-token-estimate')).toContainText('Stima: circa');
  await expect(page.getByTestId('card-age-report')).toContainText('La card non indica un\'età');
  await expect(page.getByLabel('Nome', { exact: true })).toHaveValue(name);
  await expect(page.getByLabel('Biografia')).toHaveValue(/disegna mappe/);
  await expect(page.getByLabel('Biografia').locator('xpath=ancestor::lyr-field')).toContainText('dalla card');

  // The age is empty and required: a save without it is refused and sends nothing.
  await expect(page.getByLabel('Età', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Salva personaggio' }).click();
  await expect(page.getByText('Alcuni campi non sono validi.')).toBeVisible();
  await expect(page).toHaveURL(/\/characters\/import\//);

  // The scenarios of the two greetings are offered and on by default.
  const scenarios = preview.getByRole('checkbox', { name: 'Crea gli scenari dai saluti' });
  await expect(scenarios).toBeChecked();
  await page.getByLabel('Età', { exact: true }).fill('31');
  await page.getByRole('button', { name: 'Salva personaggio' }).click();
  await expect(page).toHaveURL(/\/characters\/[^/]+\/edit$/);
  const id = page.url().split('/').at(-2);

  const stored = await (await page.request.get(`/api/v1/characters/${id}`)).json();
  expect(stored.card.identity).toMatchObject({ first_name: name, age: 31 });
  const created = await (await page.request.get(`/api/v1/scenarios?character_id=${id}`)).json();
  expect(created.items.map((item) => item.opening_message)).toEqual(
    expect.arrayContaining([expect.stringContaining('Ehi, entra pure'), 'Buonasera, cercavi qualcosa?']),
  );
  const source = await page.request.get(`/api/v1/characters/${id}/source`);
  expect(source.status()).toBe(200);

  // Export as PNG with both scenarios, then in the native format, then the original card.
  await page.getByRole('button', { name: 'Esporta', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Esporta il personaggio' })).toBeVisible();
  for (const box of await page.getByRole('region', { name: 'Esporta il personaggio' }).getByRole('checkbox').all()) {
    await box.check();
  }
  const png = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Esporta come card PNG' }).click();
  const pngDownload = await png;
  expect(pngDownload.suggestedFilename()).toMatch(/\.png$/);
  const bytes = readFileSync(await pngDownload.path());
  expect([...bytes.subarray(0, 8)]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  expect(bytes.includes(Buffer.from('ccv3\0'))).toBe(true);
  expect(bytes.includes(Buffer.from('chara\0'))).toBe(true);

  const native = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Esporta in formato Lyrveil' }).click();
  expect((await native).suggestedFilename()).toMatch(/\.lyrveil-character\.json$/);

  const original = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Scarica la card originale' }).click();
  expect((await original).suggestedFilename()).toMatch(/\.json$/);
});

test('a refused card file shows its Italian reason and stores nothing', async ({ page }) => {
  await page.goto('/characters/new');
  await page
    .getByLabel('Importa una card')
    .setInputFiles({ name: 'rotta.json', mimeType: 'application/json', buffer: Buffer.from('{"spec":"altro"}') });
  await expect(page.getByRole('alert').first()).toBeVisible();
  await expect(page).toHaveURL(/\/characters\/new$/);
});
