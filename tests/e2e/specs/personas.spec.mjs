// T-PSN-06 (P11.4): create a second persona on **Le tue persone**, start a chat with it and a note, and see both in the
// context panel; edit the note there. Through nginx and the real backend with the fake runtimes behind it.
//
// All tests share one database, so each test uses names unique to the run and looks only at them.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { openChat, startChatWithNewCharacter, uniqueName } from '../support/chats.mjs';

const WARNING = 'Ciò che scrivi qui lo sa ogni personaggio che parla con questa persona.';

function card(page, name) {
  return page.getByRole('listitem').filter({ hasText: name });
}

/** Opens the new-chat dialog from the chat list and waits until its picker is ready. */
async function openPicker(page) {
  await page.getByRole('button', { name: 'Nuova conversazione' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('Personaggio')).toBeVisible();
  return dialog;
}

/** Returns the panel that holds the conversation's details, opening it when the layout hides it. */
async function contextPanel(page) {
  const panel = page.locator('#context-panel');
  // The button exists only once the conversation has loaded, and its `aria-expanded` says whether the panel is open;
  // checking the panel itself before that would click a button that closes a panel which is about to appear.
  const toggle = page.getByRole('button', { name: 'Dettagli', exact: true });
  await expect(toggle).toBeVisible();
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') {
    await toggle.click();
  }
  await expect(panel).toBeVisible();
  return panel;
}

test('a second persona starts a chat with a note, and the context panel shows and edits them', async ({ page }) => {
  await page.goto('/chats');
  const character = uniqueName('Marta');
  await startChatWithNewCharacter(page, character);

  // The persona is created on "Le tue persone", reached from the navigation.
  await page.getByRole('link', { name: 'Le tue persone' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Le tue persone' })).toBeVisible();
  const persona = uniqueName('Giulio');
  await page.getByRole('button', { name: 'Nuova persona' }).click();

  // The form states what is shared with every character, and a name is required.
  await expect(page.getByText(WARNING)).toBeVisible();
  await page.getByRole('button', { name: 'Salva persona' }).click();
  await expect(page.getByText('Inserisci un nome da 1 a 40 caratteri.')).toBeVisible();
  await page.getByLabel('Nome', { exact: true }).fill(persona);
  await page.getByLabel('Età (facoltativa)').fill('17');
  await page.getByRole('button', { name: 'Salva persona' }).click();
  await expect(page.getByText("L'età deve essere un numero intero da 18 a 120.")).toBeVisible();
  await page.getByLabel('Età (facoltativa)').fill('34');
  await page.getByLabel('Di te').fill('Lavora come fotografo.');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
  await page.getByRole('button', { name: 'Salva persona' }).click();
  await expect(card(page, persona)).toBeVisible();
  await expect(card(page, persona).getByText('Predefinita', { exact: true })).toHaveCount(0);

  // The default persona is listed first, marked, and cannot be archived or deleted.
  const defaults = page.getByRole('listitem').filter({ has: page.getByText('Predefinita', { exact: true }) });
  await expect(defaults).toHaveCount(1);
  await expect(defaults.getByRole('button', { name: 'Archivia' })).toHaveCount(0);
  await expect(defaults.getByRole('button', { name: 'Elimina' })).toHaveCount(0);

  // The picker preselects the default persona; the new one is chosen with a note.
  await page.getByRole('link', { name: 'Chat' }).click();
  const dialog = await openPicker(page);
  const personas = await (await page.request.get('/api/v1/personas')).json();
  const defaultId = personas.items.find((item) => item.is_default).id;
  const created = personas.items.find((item) => item.name === persona);
  await expect(dialog.getByLabel('Persona', { exact: true })).toHaveValue(defaultId);
  await dialog.getByLabel('Personaggio', { exact: true }).selectOption({ label: character });
  await dialog.getByLabel('Persona', { exact: true }).selectOption({ label: persona });
  await dialog.getByLabel("Cosa sa di te all'inizio").fill('Siete colleghi da due anni.');
  await dialog.getByRole('button', { name: 'Inizia conversazione' }).click();
  await expect(dialog).toBeHidden();

  // The new chat stores both; the context panel shows the persona and the note.
  const chats = await (await page.request.get(`/api/v1/conversations?q=${character}`)).json();
  const started = chats.items.find((item) => item.persona_id === created.id);
  expect(started.persona_note).toBe('Siete colleghi da due anni.');
  await page.goto(`/chats/${started.id}`);
  const panel = await contextPanel(page);
  await expect(panel.getByTestId('context-persona')).toHaveText(persona);
  await expect(panel.getByLabel('Cosa sa di te', { exact: true })).toHaveValue('Siete colleghi da due anni.');

  // There is no way to change the persona of an existing conversation; the note can be edited.
  await expect(panel.getByLabel('Persona', { exact: true })).toHaveCount(0);
  await panel.getByLabel('Cosa sa di te', { exact: true }).fill('Siete amici da sempre.');
  await panel.getByRole('button', { name: 'Salva la nota' }).click();
  await expect(panel.getByText('Nota salvata.')).toBeVisible();
  const reread = await (await page.request.get(`/api/v1/conversations/${started.id}`)).json();
  expect(reread.persona_note).toBe('Siete amici da sempre.');
  expect(reread.persona_id).toBe(created.id);
});

test('an archived persona is hidden from the picker and one with chats cannot be deleted', async ({ page }) => {
  await page.goto('/chats');
  const character = uniqueName('Livia');
  await startChatWithNewCharacter(page, character);

  const persona = uniqueName('Elena');
  const made = await page.request.post('/api/v1/personas', { data: { name: persona } });
  expect(made.status()).toBe(201);
  const { id } = await made.json();
  const chat = await page.request.post('/api/v1/conversations', {
    data: { character_id: (await (await page.request.get('/api/v1/characters')).json()).items.find((item) => item.first_name === character).id, persona_id: id },
  });
  expect(chat.status()).toBe(201);

  await page.goto('/settings/personas');
  await expect(card(page, persona)).toContainText('1 conversazione');
  await expect(card(page, persona).getByRole('button', { name: 'Elimina' })).toHaveCount(0);
  await card(page, persona).getByRole('button', { name: 'Archivia' }).click();
  await expect(card(page, persona)).toHaveCount(0);

  await page.getByRole('link', { name: 'Chat' }).click();
  const dialog = await openPicker(page);
  await expect(dialog.getByLabel('Persona', { exact: true })).toBeVisible();
  await expect(dialog.getByLabel('Persona', { exact: true }).getByRole('option', { name: persona })).toHaveCount(0);
});
