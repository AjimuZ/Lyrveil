// The character profile of P03.2b: versions with compare and restore, duplicate, archive and purge, in the browser through
// nginx and the real backend. The character, its edit and its chat are set up through the API; every action under test
// is a click, and the stored result is read back through the API.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { uniqueName } from '../support/chats.mjs';

async function expectNoAccessibilityViolations(page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) })),
  ).toEqual([]);
}

/** Creates a character with a trait and two versions (age 27, then 28) and a chat with it; returns their ids. */
async function setUp(page, name) {
  const created = await page.request.post('/api/v1/characters', {
    data: {
      identity: { first_name: name, age: 27, grammatical_gender: 'femminile', occupation: 'Cartografa' },
      traits: [{ category: 'humor', description: 'Ironia asciutta.', locked: true }],
    },
  });
  expect(created.status()).toBe(201);
  const id = (await created.json()).id;
  const saved = await page.request.put(`/api/v1/characters/${id}`, {
    data: {
      identity: { first_name: name, age: 28, grammatical_gender: 'femminile', occupation: 'Cartografa' },
      traits: [{ category: 'humor', description: 'Ironia asciutta.', locked: true }],
    },
  });
  expect(saved.status()).toBe(200);
  const chat = await page.request.post('/api/v1/conversations', { data: { character_id: id } });
  expect(chat.status()).toBe(201);
  return { id, chatId: (await chat.json()).id };
}

const versionRow = (page, n) => page.getByTestId('versions').getByRole('listitem').filter({ hasText: `Versione ${n}` });

test('the profile compares and restores versions, duplicates, archives and purges a character (T-CHR-04, T-CHR-08)', async ({
  page,
}) => {
  const name = uniqueName('Nerea');
  const { id, chatId } = await setUp(page, name);
  await page.goto(`/characters/${id}`);
  await expect(page.getByRole('heading', { level: 1, name: 'Profilo del personaggio' })).toBeVisible();
  await expect(page.getByText('Ironia asciutta.')).toBeVisible();
  await expect(page.getByText('Questo personaggio è simulato')).toBeVisible();
  await expect(versionRow(page, 2).getByText('Attuale')).toBeVisible();
  await expect(page.getByRole('link', { name })).toBeVisible();
  await expectNoAccessibilityViolations(page);

  // Compare: version 1 against the current version 2.
  await page.getByRole('button', { name: 'Confronta la versione 1 con quella attuale' }).click();
  const row = page.getByRole('row').filter({ hasText: 'Età' });
  await expect(row).toContainText('27');
  await expect(row).toContainText('28');
  await page.getByRole('button', { name: 'Chiudi il confronto' }).click();
  await expect(page.getByRole('row').filter({ hasText: 'Età' })).toHaveCount(0);

  // Restore: a new version with the card of version 1; nothing is rewritten.
  await page.getByRole('button', { name: 'Ripristina la versione 1' }).click();
  await expect(page.getByText('Versione 1 ripristinata: ora il personaggio è alla versione 3.')).toBeVisible();
  await expect(versionRow(page, 3).getByText('Attuale')).toBeVisible();
  const restored = await (await page.request.get(`/api/v1/characters/${id}`)).json();
  expect(restored.card.identity.age).toBe(27);
  const versions = await (await page.request.get(`/api/v1/characters/${id}/versions`)).json();
  expect(versions.items.map((item) => item.version_no)).toEqual([3, 2, 1]);

  // Duplicate: the card without the conversation.
  await page.getByRole('button', { name: 'Duplica' }).click();
  await expect(page).toHaveURL(new RegExp(`/characters/(?!${id}$)[^/]+$`));
  await expect(page.getByText('Copia creata.')).toBeVisible();
  await expect(page.getByText(`${name} (copia)`).first()).toBeVisible();
  await expect(page.getByText('Ironia asciutta.')).toBeVisible();
  await expect(page.getByText('Non ci sono conversazioni attive con questo personaggio.')).toBeVisible();
  await expect(versionRow(page, 1).getByText('Duplicazione')).toBeVisible();
  const copyId = new URL(page.url()).pathname.split('/').pop();
  const copyChats = await (await page.request.get(`/api/v1/conversations?character_id=${copyId}`)).json();
  expect(copyChats.items).toEqual([]);

  // Archive hides the character and its chat; the same button brings them back.
  await page.goto(`/characters/${id}`);
  await page.getByRole('button', { name: 'Archivia', exact: true }).click();
  await expect(page.getByText('Personaggio archiviato.')).toBeVisible();
  await expect(page.getByText('Archiviato', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Nuova conversazione' })).toBeDisabled();
  const hidden = await (await page.request.get('/api/v1/characters')).json();
  expect(hidden.items.map((item) => item.id)).not.toContain(id);
  const hiddenChats = await (await page.request.get('/api/v1/conversations')).json();
  expect(hiddenChats.items.map((item) => item.id)).not.toContain(chatId);
  await page.getByRole('button', { name: "Rimuovi dall'archivio" }).click();
  await expect(page.getByText('Il personaggio è di nuovo tra quelli attivi.')).toBeVisible();
  const shown = await (await page.request.get('/api/v1/characters')).json();
  expect(shown.items.map((item) => item.id)).toContain(id);
  const shownChats = await (await page.request.get('/api/v1/conversations')).json();
  expect(shownChats.items.map((item) => item.id)).toContain(chatId);

  // Purge: a preview, the typed first name, then the character and its chat are gone.
  await page.getByRole('button', { name: 'Elimina definitivamente' }).click();
  const confirmation = page.getByRole('alertdialog');
  await expect(confirmation.getByText('1 conversazione')).toBeVisible();
  await expect(confirmation.getByText('3 versioni della scheda')).toBeVisible();
  await expectNoAccessibilityViolations(page);
  const confirm = confirmation.getByRole('button', { name: 'Elimina per sempre' });
  await expect(confirm).toBeDisabled();
  await confirmation.getByLabel(/^Per confermare scrivi il nome/).fill('altro nome');
  await expect(confirm).toBeDisabled();
  await confirmation.getByLabel(/^Per confermare scrivi il nome/).fill(name);
  await confirm.click();
  await expect(page).toHaveURL(/\/chats$/);
  expect((await page.request.get(`/api/v1/characters/${id}`)).status()).toBe(404);
  expect((await page.request.get(`/api/v1/conversations/${chatId}`)).status()).toBe(404);
  const copy = await page.request.get(`/api/v1/characters/${copyId}`);
  expect(copy.status()).toBe(200);
});
