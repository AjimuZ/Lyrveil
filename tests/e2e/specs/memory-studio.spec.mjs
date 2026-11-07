// The Memory Studio of P04.9a: list, filter, search, edit with a new revision, lock and delete memories, and the empty
// states of events, relationship and summaries, in the browser through nginx and the real backend. The character, its
// chat and its memories are set up through the API; every action under test is a click, and the stored result is read
// back through the API.
//
// The fake KoboldCpp answers every generation with one fixed text, so extraction never stores anything in this stack:
// the memories here are written by hand (`POST /memories`). Sources, episodes, state changes, summaries and an
// unresolved contradiction come from real extraction runs, and the backend integration test
// `test_memory_studio_api.py` (T-MEM-08, T-MEM-11) and the screen's unit spec cover how the Studio shows them.
// This spec was written but not run in this change.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { uniqueName } from '../support/chats.mjs';

async function expectNoAccessibilityViolations(page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) })),
  ).toEqual([]);
}

/** Creates a character, a chat with it and two written memories; returns their ids. */
async function setUp(page, name) {
  const created = await page.request.post('/api/v1/characters', {
    data: { identity: { first_name: name, age: 30, grammatical_gender: 'femminile' } },
  });
  expect(created.status()).toBe(201);
  const id = (await created.json()).id;
  const chat = await page.request.post('/api/v1/conversations', { data: { character_id: id } });
  expect(chat.status()).toBe(201);
  const chatId = (await chat.json()).id;
  const memories = {};
  for (const [key, body] of Object.entries({
    coffee: { content: 'Ama il caffè amaro.', kind: 'user_preference' },
    job: { content: 'Lavora allo Studio Rossi.', kind: 'user_fact' },
  })) {
    const memory = await page.request.post('/api/v1/memories', { data: { conversation_id: chatId, ...body } });
    expect(memory.status()).toBe(201);
    memories[key] = (await memory.json()).id;
  }
  return { id, chatId, memories };
}

const card = (page, text) => page.getByTestId('memory-card').filter({ hasText: text });

test('the Studio lists, filters, edits with a revision, locks and deletes memories (T-MEM-08)', async ({ page }) => {
  const name = uniqueName('Marta');
  const { id, memories } = await setUp(page, name);
  await page.goto(`/characters/${id}/memory`);
  await expect(page.getByRole('heading', { level: 1, name: 'Memoria del personaggio' })).toBeVisible();
  await expect(page.getByTestId('memory-card')).toHaveCount(2);

  // Origin, confidence and the empty source and history of a written memory.
  const coffee = card(page, 'Ama il caffè amaro.');
  await expect(coffee).toContainText('Confermato');
  await expect(coffee).toContainText('Affidabilità 100%');
  await expect(coffee).toContainText('Importanza 3 su 5');
  await coffee.getByRole('button', { name: 'Fonti e cronologia' }).click();
  await expect(coffee.getByTestId('memory-details')).toContainText('Nessun messaggio di origine');
  await expect(coffee.getByTestId('memory-details')).toContainText('Nessuna modifica');
  await expectNoAccessibilityViolations(page);
  await coffee.getByRole('button', { name: 'Nascondi fonti e cronologia' }).click();

  // Filter by kind, then search by a word of the text.
  await page.getByLabel('Tipo').selectOption({ label: 'Preferenza' });
  await expect(page.getByTestId('memory-card')).toHaveCount(1);
  await expect(card(page, 'Ama il caffè amaro.')).toBeVisible();
  await page.getByLabel('Tipo').selectOption({ label: 'Tutti i tipi' });
  await expect(page.getByTestId('memory-card')).toHaveCount(2);
  await page.getByLabel('Cerca nei ricordi e negli eventi').fill('Rossi');
  await expect(page.getByTestId('memory-card')).toHaveCount(1);
  await expect(card(page, 'Lavora allo Studio Rossi.')).toBeVisible();
  await page.getByLabel('Cerca nei ricordi e negli eventi').fill('');
  await expect(page.getByTestId('memory-card')).toHaveCount(2);

  // Edit: the text changes, the memory moves to version 2 and the history shows what it said before.
  await coffee.getByRole('button', { name: 'Modifica' }).click();
  await page.getByLabel('Testo del ricordo').fill('Ama il caffè con poco zucchero.');
  await page.getByRole('button', { name: 'Salva' }).click();
  const edited = card(page, 'Ama il caffè con poco zucchero.');
  await expect(edited).toBeVisible();
  await edited.getByRole('button', { name: 'Fonti e cronologia' }).click();
  const history = edited.getByTestId('memory-history');
  await expect(history).toContainText('Versione 1');
  await expect(history).toContainText('Diceva: Ama il caffè amaro.');
  const revisions = await (await page.request.get(`/api/v1/memories/${memories.coffee}/revisions`)).json();
  expect(revisions.items.map((item) => [item.version, item.actor, item.reason])).toEqual([[1, 'user', 'edit']]);

  // A refused edit shows the server's reason and keeps the text.
  await edited.getByRole('button', { name: 'Modifica' }).click();
  await page.getByLabel('Testo del ricordo').fill('Lavora allo Studio Rossi.');
  await page.getByRole('button', { name: 'Salva' }).click();
  await expect(page.getByRole('alert')).toContainText('Esiste già un ricordo con lo stesso contenuto.');
  await page.getByRole('button', { name: 'Annulla' }).click();

  // Lock: the pill appears and the lock filter finds it.
  await edited.getByRole('button', { name: 'Blocca' }).click();
  await expect(edited).toContainText('Bloccato');
  await expect(edited.getByRole('button', { name: 'Sblocca' })).toBeVisible();
  await page.getByLabel('Blocco').selectOption({ label: 'Bloccati' });
  await expect(page.getByTestId('memory-card')).toHaveCount(1);
  await page.getByLabel('Blocco').selectOption({ label: 'Tutti' });
  await expect(page.getByTestId('memory-card')).toHaveCount(2);
  const locked = await (await page.request.get(`/api/v1/memories/${memories.coffee}`)).json();
  expect(locked.locked).toBe(true);

  // Delete needs a confirmation; afterwards the memory is gone.
  const job = card(page, 'Lavora allo Studio Rossi.');
  await job.getByRole('button', { name: 'Elimina' }).click();
  await expect(job).toContainText('Eliminare questo ricordo?');
  await job.getByRole('button', { name: 'Sì, elimina' }).click();
  await expect(page.getByTestId('memory-card')).toHaveCount(1);
  expect((await page.request.get(`/api/v1/memories/${memories.job}`)).status()).toBe(404);
});

test('events, relationship and summaries need a chat and say so when nothing was recorded yet', async ({ page }) => {
  const name = uniqueName('Irene');
  const { id } = await setUp(page, name);
  await page.goto(`/characters/${id}/memory`);

  await page.getByRole('button', { name: 'Rapporto' }).click();
  await expect(page.getByText('Scegli una conversazione per vedere questa sezione.')).toBeVisible();
  await page.getByLabel('Conversazione').selectOption({ label: name });
  await expect(page.getByText('Il rapporto non è ancora stato registrato')).toBeVisible();
  await expect(page.getByText('Nessun cambiamento registrato.')).toBeVisible();

  await page.getByRole('button', { name: 'Eventi' }).click();
  await expect(page.getByText('Non ci sono eventi ricordati.')).toBeVisible();
  await page.getByRole('button', { name: 'Riassunti' }).click();
  await expect(page.getByText('Non ci sono ancora riassunti per questa conversazione.')).toBeVisible();
  await expectNoAccessibilityViolations(page);
});
