// The conversation screen of P02.6b through nginx and the real backend: markup as text (T-SEC-03), edit and delete
// (T-MSG-05, T-MSG-06), and history paging with recovery after a reload (T-MSG-08).
//
// Replies, alternatives, a reload while streaming, simultaneous chats and reconnecting are in
// conversation-stream.spec.mjs. The messages sent here also get a reply from the stack's fake runtime.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { bubble, openChat, sendText, startChatWithNewCharacter, uniqueName } from '../support/chats.mjs';

test.beforeEach(async ({ page }) => {
  await page.goto('/chats');
  await expect(page.getByRole('heading', { level: 1, name: 'Conversazioni' })).toBeVisible();
});

test('markup in messages renders as text (T-SEC-03)', async ({ page }) => {
  const name = uniqueName('Sofia');
  await startChatWithNewCharacter(page, name);
  await openChat(page, name);

  const markup = '<img src="x" onerror="window.injected = true"><b>grassetto</b> &amp; <script>window.injected = true</script>';
  await sendText(page, markup);

  // The characters are on the page as text, and nothing was parsed into elements or ran.
  const sent = bubble(page, 'grassetto');
  await expect(sent).toContainText(markup);
  await expect(sent.locator('img, b, script')).toHaveCount(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
});

test('a message can be edited and deleted, and both survive a reload (T-MSG-05, T-MSG-06)', async ({ page }) => {
  const name = uniqueName('Irene');
  await startChatWithNewCharacter(page, name);
  await openChat(page, name);

  await sendText(page, 'Prima versione');
  // While a message is edited its text is in a field, so the bubble is found by its place: the user's first message.
  const message = page.locator('li.message.mine').first();
  await expect(message).toContainText('Prima versione');
  // The sender sees one check until the server marks the message read.
  await expect(message.getByRole('img', { name: 'Inviato' })).toBeVisible();

  await message.getByRole('button', { name: 'Modifica' }).click();
  const editor = message.getByLabel('Testo del messaggio');
  await expect(editor).toHaveValue('Prima versione');
  await editor.fill('Seconda versione');
  await message.getByRole('button', { name: 'Salva modifica' }).click();
  await expect(message).toContainText('Seconda versione');
  await expect(message).toContainText('modificato');
  await expect(bubble(page, 'Prima versione')).toHaveCount(0);

  // Cancelling an edit leaves the text alone.
  await message.getByRole('button', { name: 'Modifica' }).click();
  await message.getByLabel('Testo del messaggio').fill('Mai salvata');
  await message.getByRole('button', { name: 'Annulla' }).click();
  await expect(message).toContainText('Seconda versione');
  await expect(bubble(page, 'Mai salvata')).toHaveCount(0);

  await page.reload();
  await expect(bubble(page, 'Seconda versione')).toContainText('modificato');

  // Delete asks first; declining keeps the message, confirming removes it for good.
  await bubble(page, 'Seconda versione').getByRole('button', { name: 'Elimina' }).click();
  await bubble(page, 'Seconda versione').getByRole('button', { name: 'Annulla' }).click();
  await expect(bubble(page, 'Seconda versione')).toBeVisible();
  await bubble(page, 'Seconda versione').getByRole('button', { name: 'Elimina' }).click();
  await bubble(page, 'Seconda versione').getByRole('button', { name: 'Elimina messaggio' }).click();
  await expect(bubble(page, 'Seconda versione')).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
  await expect(bubble(page, 'Seconda versione')).toHaveCount(0);
});

test('the composer sends with Enter, keeps a line break with Shift+Enter and counts near the limit', async ({ page }) => {
  const name = uniqueName('Greta');
  await startChatWithNewCharacter(page, name);
  await openChat(page, name);

  const composer = page.getByLabel('Scrivi un messaggio');
  const send = page.getByRole('button', { name: 'Invia', exact: true });
  await expect(send).toBeDisabled();

  await composer.fill('riga uno');
  await composer.press('Shift+Enter');
  await composer.pressSequentially('riga due');
  await expect(composer).toHaveValue('riga uno\nriga due');
  await composer.press('Enter');
  await expect(composer).toHaveValue('');
  await expect(page.getByText('riga due')).toBeVisible();

  // Over the limit nothing is sent, the text stays and the reason is shown.
  await composer.fill('x'.repeat(8001));
  await expect(page.getByText('Il messaggio supera il limite di 8000 caratteri.')).toBeVisible();
  await expect(send).toBeDisabled();
  await composer.fill('x'.repeat(7500));
  await expect(page.getByText('7500 / 8000')).toBeVisible();
  await expect(send).toBeEnabled();
});

test('history loads page by page, with day separators, and a reload brings the newest page back (T-MSG-08)', async ({ page }) => {
  const name = uniqueName('Nora');
  await startChatWithNewCharacter(page, name);
  const id = await openChat(page, name);

  // 30 messages and their 30 reply placeholders are 60 messages: the newest page holds 50 and the oldest 5 messages
  // of the user are on the next one.
  await page.evaluate(
    async ({ conversation, count }) => {
      for (let number = 1; number <= count; number += 1) {
        const response = await fetch(`/api/v1/conversations/${conversation}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ client_message_id: `paging-${conversation}-${number}`, content: `Numero ${number}`, intent: 'text' }),
        });
        if (response.status !== 202) {
          throw new Error(`send ${number} answered ${response.status}`);
        }
      }
    },
    { conversation: id, count: 30 },
  );

  await page.reload();
  await expect(page.getByText('Numero 30', { exact: true })).toBeVisible();
  await expect(page.getByText('Numero 1', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('list', { name: 'Messaggi' }).locator('li.day')).toHaveCount(1);

  // The older page loads from the button, or by itself when the list is scrolled to its top, which can happen first
  // while the replies stream in and change the height; either way it arrives once and the button is gone.
  const older = page.getByRole('button', { name: 'Mostra messaggi precedenti' });
  await expect(async () => {
    if (await older.isVisible()) {
      await older.click({ timeout: 1000 });
    }
    await expect(page.getByText('Numero 1', { exact: true })).toBeAttached({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  await expect(older).toHaveCount(0);

  // Each message is shown once, however many pages were loaded.
  await expect(page.getByText('Numero 30', { exact: true })).toHaveCount(1);
  await page.reload();
  await expect(page.getByText('Numero 30', { exact: true })).toBeVisible();
  await expect(page.getByText('Numero 30', { exact: true })).toHaveCount(1);
});

test('a chat that does not exist says so and leads back to the list', async ({ page }) => {
  await page.goto('/chats/00000000-0000-7000-8000-000000000000');
  await expect(page.getByText('Elemento non trovato.')).toBeVisible();
  await page.getByRole('link', { name: 'Torna alle conversazioni' }).click();
  await expect(page).toHaveURL(/\/chats$/);
});
