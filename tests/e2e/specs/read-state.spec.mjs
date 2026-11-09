// T-MSG-10 (P07.6) through nginx and the real backend: the read state of the user's messages, and the unread count and
// preview of a chat-list row. The stack's fake runtime answers every message.
import { expect, test } from '@playwright/test';

import { openChat, sendText, startChatWithNewCharacter, uniqueName } from '../support/chats.mjs';

/** Resolves when the screen has reported the chat `id` as read and the server accepted it. */
function readMarked(page, id) {
  return page.waitForResponse(
    (response) => response.url().endsWith(`/api/v1/conversations/${id}/read`) && response.status() === 200,
  );
}

test('a reply that arrives while the list is open is unread until the chat is opened (T-MSG-10)', async ({ page }) => {
  await page.goto('/chats');
  const name = uniqueName('Livia');
  await startChatWithNewCharacter(page, name);
  const id = await openChat(page, name);

  // The message is checked twice once the character starts answering, and a reply read on screen leaves nothing unread.
  const marked = readMarked(page, id);
  await sendText(page, 'Ci sei?');
  const message = page.locator('li.message.mine').first();
  await expect(message.getByRole('img', { name: 'Letto' })).toBeVisible();
  await marked;
  await page.getByRole('link', { name: 'Torna alle conversazioni' }).click();
  const row = page.getByRole('listitem').filter({ hasText: name });
  await expect(row.getByTestId('chat-preview')).toBeVisible();
  await expect(row.getByTestId('chat-unread')).toHaveCount(0);

  // A reply that finishes while the list is on screen is unread, and the row follows the event without a reload.
  const sent = await page.request.post(`/api/v1/conversations/${id}/messages`, { data: { content: 'Sei ancora lì?' } });
  expect(sent.status()).toBe(202);
  await expect(row.getByTestId('chat-unread')).toHaveAccessibleName('1 messaggio non letto', { timeout: 30_000 });
  await expect(row.getByTestId('chat-preview')).toBeVisible();

  // Opening the chat reads it: the list no longer shows the count, also after a reload.
  const opened = readMarked(page, id);
  await row.getByRole('link').click();
  await opened;
  await page.getByRole('link', { name: 'Torna alle conversazioni' }).click();
  await expect(row.getByTestId('chat-preview')).toBeVisible();
  await expect(row.getByTestId('chat-unread')).toHaveCount(0);
  await page.reload();
  await expect(row.getByTestId('chat-preview')).toBeVisible();
  await expect(row.getByTestId('chat-unread')).toHaveCount(0);
});
