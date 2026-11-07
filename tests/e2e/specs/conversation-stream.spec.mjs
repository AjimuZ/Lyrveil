// Replies on the conversation screen in the Compose test project (P02.6b with P02.6c): the fake KoboldCpp streams
// 'Ciao, sono qui.' for every reply, so these tests check what the screen does with a live, stored reply.
//   T-MSG-03 streaming and the stored final text, T-MSG-04 regenerate and alternatives, T-MSG-07 a reload while a reply
//   streams, T-MSG-09 two conversations with no mixed text, T-MSG-12 reconnecting the WebSocket.
//
// The fake's reply takes about a second, too short to catch half way by timing. T-MSG-07 and T-MSG-12 therefore stand
// between the page and /ws with Playwright's WebSocket routing, which keeps frames back or cuts the connection, and let
// the real backend and orchestrator do everything else. The store's unit tests cover a reply that is half stored.
import { expect, test } from '@playwright/test';

import { openChat, sendText, startChatWithNewCharacter, uniqueName } from '../support/chats.mjs';

const REPLY = 'Ciao, sono qui.';

test.beforeEach(async ({ page }) => {
  await page.goto('/chats');
  await expect(page.getByRole('heading', { level: 1, name: 'Conversazioni' })).toBeVisible();
});

/** The bubbles written by the character. */
function replies(page) {
  return page.locator('li.message:not(.mine)');
}

test('a reply streams in and is stored with its final text (T-MSG-03)', async ({ page }) => {
  const name = uniqueName('Lia');
  await startChatWithNewCharacter(page, name);
  await openChat(page, name);

  await sendText(page, 'Ciao! Come stai?');
  await expect(replies(page)).toHaveCount(1);
  await expect(replies(page).first()).toContainText(REPLY, { timeout: 30_000 });
  // The user's message shows its check and the typing indicator is gone with the finished reply.
  await expect(page.getByRole('status', { name: `${name} sta scrivendo` })).toHaveCount(0);

  // The stored reply is the same text, once, after a reload.
  await page.reload();
  await expect(replies(page)).toHaveCount(1);
  await expect(replies(page).first()).toContainText(REPLY);
});

test('Rigenera adds an alternative that can be stepped through, and the choice survives a reload (T-MSG-04)', async ({ page }) => {
  const name = uniqueName('Marta');
  await startChatWithNewCharacter(page, name);
  await openChat(page, name);

  await sendText(page, 'Dimmi qualcosa');
  await expect(replies(page).first()).toContainText(REPLY, { timeout: 30_000 });
  // One version only: no alternatives to choose from yet.
  await expect(page.getByRole('group', { name: /Alternativa \d di \d/ })).toHaveCount(0);

  await replies(page).first().getByRole('button', { name: 'Rigenera' }).click();
  const group = page.getByRole('group', { name: 'Alternativa 2 di 2' });
  await expect(group).toBeVisible({ timeout: 30_000 });
  await expect(replies(page).first()).toContainText(REPLY);
  await expect(replies(page)).toHaveCount(1);

  await group.getByRole('button', { name: 'Alternativa precedente' }).click();
  await expect(page.getByRole('group', { name: 'Alternativa 1 di 2' })).toBeVisible();
  await page.getByRole('button', { name: 'Alternativa successiva' }).click();
  await expect(page.getByRole('group', { name: 'Alternativa 2 di 2' })).toBeVisible();

  await page.getByRole('button', { name: 'Alternativa precedente' }).click();
  await expect(page.getByRole('group', { name: 'Alternativa 1 di 2' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('group', { name: 'Alternativa 1 di 2' })).toBeVisible();
  await expect(replies(page)).toHaveCount(1);
});

test('a reload while a reply streams brings back the whole reply once (T-MSG-07)', async ({ page }) => {
  // The first connection of the page sees the first piece of the reply and nothing after it, as if the tab were
  // closed while the text was arriving. The connection made after the reload is not touched.
  let connections = 0;
  await page.routeWebSocket(/\/ws$/, (socket) => {
    connections += 1;
    const server = socket.connectToServer();
    socket.onMessage((message) => server.send(message));
    if (connections > 1) {
      server.onMessage((message) => socket.send(message));
      return;
    }
    let held = false;
    server.onMessage((message) => {
      if (held) {
        return;
      }
      socket.send(message);
      held = typeof message === 'string' && JSON.parse(message).type === 'message.delta';
    });
  });

  const name = uniqueName('Elena');
  await startChatWithNewCharacter(page, name);
  await openChat(page, name);

  await sendText(page, 'Raccontami');
  await expect(replies(page).first().locator('.text')).toBeVisible({ timeout: 30_000 });

  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
  await expect(replies(page)).toHaveCount(1);
  await expect(replies(page).first().locator('.text')).toHaveText(REPLY, { timeout: 30_000 });
  await expect(page.getByRole('status', { name: `${name} sta scrivendo` })).toHaveCount(0);
});

test('two conversations get their own replies, with no mixed text (T-MSG-09)', async ({ page }) => {
  const first = uniqueName('Anna');
  const second = uniqueName('Bruno');
  await startChatWithNewCharacter(page, first);
  await startChatWithNewCharacter(page, second);

  await openChat(page, first);
  await sendText(page, 'Messaggio per la prima');
  await page.getByRole('link', { name: 'Torna alle conversazioni' }).click();

  // The second chat is opened and written to while the first reply may still be produced.
  await openChat(page, second);
  await sendText(page, 'Messaggio per la seconda');
  await expect(replies(page).first()).toContainText(REPLY, { timeout: 30_000 });
  await expect(page.getByText('Messaggio per la prima')).toHaveCount(0);
  await expect(replies(page)).toHaveCount(1);

  await page.getByRole('link', { name: 'Torna alle conversazioni' }).click();
  await openChat(page, first);
  await expect(page.getByText('Messaggio per la prima')).toBeVisible();
  await expect(page.getByText('Messaggio per la seconda')).toHaveCount(0);
  await expect(replies(page)).toHaveCount(1);
  await expect(replies(page).first()).toContainText(REPLY, { timeout: 30_000 });
  await expect(replies(page).first().locator('.text')).toHaveText(REPLY);
});

test('a reply written while the WebSocket is down appears after it reconnects (T-MSG-12)', async ({ page }) => {
  // The connection is cut once the server has greeted the page, and the message is sent at once: the page reconnects
  // after about a second, and the reply is produced in the meantime.
  let connections = 0;
  let cut = null;
  let greeted = false;
  await page.routeWebSocket(/\/ws$/, (socket) => {
    connections += 1;
    const server = socket.connectToServer();
    socket.onMessage((message) => server.send(message));
    server.onMessage((message) => {
      socket.send(message);
      if (connections === 1 && typeof message === 'string' && JSON.parse(message).type === 'connected') {
        cut = () => {
          server.close();
          socket.close();
        };
        greeted = true;
      }
    });
  });

  const name = uniqueName('Chiara');
  await startChatWithNewCharacter(page, name);
  await openChat(page, name);
  await expect.poll(() => greeted).toBe(true);

  cut();
  await sendText(page, 'Ci sei?');
  await expect(replies(page)).toHaveCount(1);
  await expect(replies(page).first()).toContainText(REPLY, { timeout: 30_000 });
  expect(connections).toBeGreaterThan(1);
  await expect(replies(page)).toHaveCount(1);
});
