// P03.7a: several conversations per character, and the scenario screen (create, edit, search, archive, delete) with a
// new chat started from a scenario, through nginx and the real backend with the fake runtimes behind it.
//
// All tests share one database, so each test uses names unique to the run and looks only at them.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { bubble, openChat, startChatWithNewCharacter, uniqueName } from '../support/chats.mjs';

const OPENING = 'Hai fatto tardi, pensavo non venissi più.';

function card(page, title) {
  return page.getByRole('listitem').filter({ hasText: title });
}

/** Opens the new-chat dialog from the chat list and waits until its picker is ready. */
async function openPicker(page) {
  await page.getByRole('button', { name: 'Nuova conversazione' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('Personaggio')).toBeVisible();
  return dialog;
}

test('a character can have several conversations and the list shows each one', async ({ page }) => {
  await page.goto('/chats');
  const name = uniqueName('Irene');
  await startChatWithNewCharacter(page, name);

  const dialog = await openPicker(page);
  await dialog.getByLabel('Personaggio').selectOption({ label: name });
  await dialog.getByRole('button', { name: 'Inizia conversazione' }).click();
  await expect(dialog).toBeHidden();
  await expect(card(page, name)).toHaveCount(2);

  // The two chats are stored as two conversations of one character.
  const characters = await (await page.request.get('/api/v1/characters')).json();
  const character = characters.items.find((item) => item.first_name === name);
  const chats = await (await page.request.get(`/api/v1/conversations?character_id=${character.id}`)).json();
  expect(chats.items).toHaveLength(2);
});

test('scenarios are created, edited, searched, archived and deleted, and start a chat', async ({ page }) => {
  await page.goto('/chats');
  const name = uniqueName('Sofia');
  await startChatWithNewCharacter(page, name);
  const title = uniqueName('Faro');

  await page.getByRole('link', { name: 'Scenari' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Scenari' })).toBeVisible();
  await expect(page.getByText('Le conversazioni già iniziate tengono la loro copia')).toBeVisible();

  // The title is required, and a refusal of the form shows its reason.
  await page.getByRole('button', { name: 'Nuovo scenario' }).click();
  await page.getByRole('button', { name: 'Salva scenario' }).click();
  await expect(page.getByText('Il titolo deve avere da 1 a 200 caratteri.')).toBeVisible();
  await page.getByLabel('Titolo').fill(title);
  await page.getByLabel('Descrizione').fill('Una sera di pioggia.');
  await page.getByLabel('Messaggio iniziale').fill(OPENING);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
  await page.getByRole('button', { name: 'Salva scenario' }).click();
  await expect(card(page, title)).toBeVisible();
  await expect(card(page, title)).toContainText('Qualsiasi personaggio');

  // Edit replaces the fields; search finds it by its text.
  await card(page, title).getByRole('button', { name: 'Modifica' }).click();
  await page.getByLabel('Descrizione').fill('Una mattina di vento.');
  await page.getByRole('button', { name: 'Salva scenario' }).click();
  await expect(card(page, title)).toContainText('Una mattina di vento.');
  await page.getByLabel('Cerca negli scenari').fill('mattina vento');
  await expect(card(page, title)).toBeVisible();
  await page.getByLabel('Cerca negli scenari').fill('parolainesistentexyz');
  await expect(page.getByText('Nessuno scenario corrisponde alla ricerca.')).toBeVisible();
  await page.getByLabel('Cerca negli scenari').fill('');

  // A new chat starts from the scenario: its opening message is the first message.
  await card(page, title).getByRole('link', { name: 'Inizia conversazione' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('Scenario')).toHaveValue(/.+/);
  await dialog.getByLabel('Personaggio').selectOption({ label: name });
  await dialog.getByLabel('Scenario').selectOption({ label: title });
  await dialog.getByRole('button', { name: 'Inizia conversazione' }).click();
  await expect(dialog).toBeHidden();
  await openChat(page, name);
  await expect(bubble(page, OPENING)).toBeVisible();

  // Archive moves the scenario to its tab; delete asks first and leaves the running chat alone.
  await page.getByRole('link', { name: 'Scenari' }).click();
  await card(page, title).getByRole('button', { name: 'Archivia' }).click();
  await expect(card(page, title)).toBeHidden();
  await page.getByRole('button', { name: 'Archiviati' }).click();
  await card(page, title).getByRole('button', { name: 'Elimina' }).click();
  await expect(page.getByText(`Eliminare lo scenario «${title}»?`)).toBeVisible();
  await card(page, title).getByRole('button', { name: 'Elimina scenario' }).click();
  await expect(card(page, title)).toBeHidden();

  await page.goto('/chats');
  await openChat(page, name);
  await expect(bubble(page, OPENING)).toBeVisible();
});
