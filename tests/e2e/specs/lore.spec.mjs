// T-LOR-07 (P12.5b): create a world, add an entry, link it to two characters, use **Prova le parole chiave** and see the
// entry under **Voci di lore** of a reply; then remove one link and see that the next reply no longer reads the world.
// Through nginx and the real backend with the fake runtimes behind it.
//
// All tests share one database, so each test uses names unique to the run and looks only at them. The characters and
// their chat are set up through the API; the world, its entry, the links and the keyword test are driven in the browser.
// The diagnostic mode is turned on through `PATCH /settings` for the one reply that is inspected and off again after.
// This spec was written but not run in this change.
import { expect, test } from '@playwright/test';

import { uniqueName } from '../support/chats.mjs';

async function makeCharacter(page, name) {
  const created = await page.request.post('/api/v1/characters', {
    data: { identity: { first_name: name, age: 30, grammatical_gender: 'femminile' } },
  });
  expect(created.status()).toBe(201);
  return (await created.json()).id;
}

/** Sends a message and waits until the reply is complete; returns the reply's id. */
async function sendAndWait(page, chatId, content) {
  const sent = await page.request.post(`/api/v1/conversations/${chatId}/messages`, { data: { content } });
  expect(sent.status()).toBe(202);
  const replyId = (await sent.json()).reply.id;
  await expect
    .poll(
      async () => {
        const latest = await (await page.request.get(`/api/v1/conversations/${chatId}/messages`)).json();
        return latest.items.find((item) => item.id === replyId)?.status;
      },
      { timeout: 60_000 },
    )
    .toBe('complete');
  return replyId;
}

test('a world with an entry is linked to two characters, tested, and read by a reply until a link is removed (T-LOR-07)', async ({
  page,
}) => {
  const first = uniqueName('Marta');
  const second = uniqueName('Livia');
  const world = uniqueName('Aldara');
  const firstId = await makeCharacter(page, first);
  const secondId = await makeCharacter(page, second);
  const chat = await page.request.post('/api/v1/conversations', { data: { character_id: firstId } });
  expect(chat.status()).toBe(201);
  const chatId = (await chat.json()).id;
  const setMode = (on) => page.request.patch('/api/v1/settings', { data: { 'app.diagnostic_mode': on } });

  // The world is created from the list and opens in its editor.
  await page.goto('/worlds');
  await page.getByRole('button', { name: 'Nuovo mondo' }).click();
  await page.getByLabel('Nome', { exact: true }).fill(world);
  await page.getByRole('button', { name: 'Crea mondo' }).click();
  await expect(page).toHaveURL(/\/worlds\/[^/]+$/);
  const worldId = new URL(page.url()).pathname.split('/').pop();
  await expect(page.getByRole('button', { name: 'Nuova voce' })).toBeVisible();

  // An entry with one key.
  await page.getByRole('button', { name: 'Nuova voce' }).click();
  await page.getByLabel('Titolo', { exact: true }).fill('Il faro');
  await page.getByLabel('Contenuto', { exact: true }).fill('Il faro di Aldara ha una luce bianca che si vede da lontano.');
  await page.getByLabel('Parole chiave', { exact: true }).fill('faro');
  await page.getByRole('button', { name: 'Salva voce' }).click();
  await expect(page.getByRole('listitem').filter({ hasText: 'Il faro di Aldara' })).toBeVisible();

  // The world is linked to both characters from its editor.
  const targets = page.getByTestId('world-targets');
  await expect(targets.getByText('Questo mondo non è collegato a nulla: nessuna risposta lo legge.')).toBeVisible();
  for (const name of [first, second]) {
    await targets.getByLabel('Destinazione', { exact: true }).selectOption({ label: name });
    await targets.getByRole('button', { name: 'Collega', exact: true }).click();
    await expect(targets.getByTestId('world-target').filter({ hasText: name })).toBeVisible();
  }
  const links = await (await page.request.get(`/api/v1/lore/books/${worldId}/links`)).json();
  expect(links.items.map((item) => item.character_id).sort()).toEqual([firstId, secondId].sort());

  // **Prova le parole chiave**: the pasted text activates the entry because of its key; a text without it does not.
  const keywordTest = page.getByTestId('keyword-test');
  await keywordTest.getByLabel('Testo da provare').fill('Vedo un faro in lontananza.');
  await keywordTest.getByRole('button', { name: 'Prova il testo' }).click();
  const entry = keywordTest.getByTestId('keyword-test-entry').filter({ hasText: 'Il faro' });
  await expect(entry).toHaveAttribute('data-active', 'true');
  await expect(entry).toContainText('Compare la parola chiave «faro».');
  await expect(keywordTest.getByTestId('keyword-test-summary')).toContainText('1 su 1 voci');
  await keywordTest.getByLabel('Testo da provare').fill('Il mare è calmo oggi.');
  await keywordTest.getByRole('button', { name: 'Prova il testo' }).click();
  await expect(entry).toHaveAttribute('data-active', 'false');
  await expect(entry).toContainText('Nessuna parola chiave compare nel testo.');

  // A reply to a message with the key reads the entry, and diagnostic mode lists it under **Voci di lore**.
  try {
    expect((await setMode(true)).status()).toBe(200);
    const firstReply = await sendAndWait(page, chatId, 'Hai visto il faro?');
    await page.goto(`/characters/${firstId}/memory?conversation=${chatId}`);
    await page.getByRole('button', { name: 'Diagnostica' }).click();
    await page.getByLabel('Risposta', { exact: true }).selectOption({ index: 1 });
    await expect(page.getByRole('heading', { name: 'Voci di lore' })).toBeVisible();
    const used = page.getByTestId('diagnostics-lore-entry').filter({ hasText: 'Il faro' });
    await expect(used).toBeVisible();
    await expect(used).toContainText(world);
    await expect(used).toContainText('Parola chiave: faro');
    const stored = await (await page.request.get(`/api/v1/messages/${firstReply}/diagnostics`)).json();
    expect(stored.lore_refs.map((ref) => ref.title)).toEqual(['Il faro']);

    // The character's profile lists the world and unlinks it; the world and the other link stay.
    await page.goto(`/characters/${firstId}`);
    const linked = page.getByTestId('world-links');
    await expect(linked.getByTestId('world-link').filter({ hasText: world })).toBeVisible();
    await linked.getByRole('button', { name: `Scollega il mondo «${world}»` }).click();
    await expect(linked.getByText('Nessun mondo collegato.')).toBeVisible();
    const afterLinks = await (await page.request.get(`/api/v1/lore/links?character_id=${firstId}`)).json();
    expect(afterLinks.items).toEqual([]);
    const remaining = await (await page.request.get(`/api/v1/lore/books/${worldId}/links`)).json();
    expect(remaining.items.map((item) => item.character_id)).toEqual([secondId]);
    expect((await page.request.get(`/api/v1/lore/books/${worldId}`)).status()).toBe(200);

    // The next reply to the same kind of message no longer reads the world.
    const secondReply = await sendAndWait(page, chatId, 'Il faro è ancora acceso?');
    const next = await (await page.request.get(`/api/v1/messages/${secondReply}/diagnostics`)).json();
    expect(next.lore_refs).toEqual([]);
  } finally {
    await setMode(false);
  }
});
