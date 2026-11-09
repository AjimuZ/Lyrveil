// P07.5: in-app notifications in the Compose test project: the unread badge on the bell, the list under it, the brief
// toast of a new notice, and marking read.
//
// The proactive feature is off by default and has no screen that switches it on yet, so the real backend makes no
// notification in this project. The notification store of the backend is played by the browser's side of the
// connection: a route answers GET /notifications and the two read routes from a list the test keeps, and Playwright's
// WebSocket routing delivers a scripted `notification.created` frame beside the real backend's own frames. The backend
// half (the routes, the stored rows and the published event) is `backend/tests/integration/test_notifications.py`.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

// Injected events use ids far above the real ones, so the client's replay check (an event counts only if its id is
// newer than the last delivered) keeps accepting them in order.
let injectedId = 2_000_000_000;

function notice(id, overrides = {}) {
  return {
    id,
    kind: 'missed_events',
    conversation_id: null,
    message_id: null,
    title: 'Messaggi programmati scaduti',
    body: "All'avvio 2 messaggi programmati risultavano scaduti da più di 10 minuti.",
    read: false,
    read_at: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

/** Answers the notification routes from `items` (newest first), which the routes also change, like the server does. */
async function serveNotifications(page, items) {
  await page.route(/\/api\/v1\/notifications(\/|\?|$)/, async (route) => {
    const request = route.request();
    const { pathname } = new URL(request.url());
    const unread = () => items.filter((item) => !item.read).length;
    if (request.method() === 'GET') {
      await route.fulfill({ json: { items, next_cursor: null, unread_count: unread() } });
      return;
    }
    const stamp = new Date().toISOString();
    if (pathname.endsWith('/read-all')) {
      const changed = items.filter((item) => !item.read);
      for (const item of changed) {
        Object.assign(item, { read: true, read_at: stamp });
      }
      await route.fulfill({ json: { updated: changed.length } });
      return;
    }
    const target = items.find((item) => pathname.endsWith(`/${item.id}/read`));
    if (target === undefined) {
      await route.fulfill({ status: 404, json: { code: 'NOT_FOUND', detail: 'Notifica non trovata.' } });
      return;
    }
    Object.assign(target, { read: true, read_at: target.read_at ?? stamp });
    await route.fulfill({ json: target });
  });
}

/** Stands between the page and /ws, and returns a function that delivers one event frame to the page. */
async function routeSocket(page) {
  let toPage = null;
  await page.routeWebSocket(/\/ws$/, (socket) => {
    toPage = socket;
    const server = socket.connectToServer();
    socket.onMessage((message) => server.send(message));
    server.onMessage((message) => socket.send(message));
  });
  return async (type, data) => {
    await expect.poll(() => toPage !== null).toBe(true);
    injectedId += 1;
    toPage.send(JSON.stringify({ v: 1, id: injectedId, type, ts: new Date().toISOString(), data }));
  };
}

const bell = (page) => page.getByRole('button', { name: /^Notifiche/ });

test('the badge counts the unread notices and the list shows them, newest first', async ({ page }) => {
  await serveNotifications(page, [
    notice('n2', { title: 'Alba', body: 'Ti penso, come stai?' }),
    notice('n1', { read: true, read_at: new Date().toISOString() }),
  ]);
  await page.goto('/chats');

  await expect(bell(page)).toHaveAccessibleName('Notifiche: 1 non letta');
  await expect(page.getByTestId('notification-badge')).toHaveText('1');

  await bell(page).click();
  const panel = page.getByRole('region', { name: 'Notifiche' });
  const rows = panel.getByRole('listitem');
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText('Alba');
  await expect(rows.first()).toContainText('Ti penso, come stai?');
  await expect(rows.first()).toContainText('Non letta');
  await expect(rows.last()).not.toContainText('Non letta');

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
});

test('a new notice shows a toast and raises the badge, and the toast hides by itself', async ({ page }) => {
  await serveNotifications(page, []);
  const deliver = await routeSocket(page);
  await page.goto('/chats');
  await expect(page.getByTestId('notification-badge')).toHaveCount(0);

  const created = notice('n1', { kind: 'proactive_message', title: 'Alba', body: 'Ti è venuto in mente il libro?' });
  await deliver('notification.created', { notification: created });

  const toast = page.getByTestId('notification-toast');
  await expect(toast).toContainText('Alba');
  await expect(toast).toContainText('Ti è venuto in mente il libro?');
  await expect(page.getByTestId('notification-badge')).toHaveText('1');

  // The notice stays in the list after the toast is gone.
  await expect(toast).toHaveCount(0, { timeout: 15_000 });
  await bell(page).click();
  await expect(page.getByRole('region', { name: 'Notifiche' }).getByRole('listitem')).toHaveCount(1);
  await expect(page.getByTestId('notification-badge')).toHaveText('1');
});

test('marking one notice read lowers the badge, and the choice survives a reload', async ({ page }) => {
  await serveNotifications(page, [notice('n2'), notice('n1', { title: 'Alba', body: 'Ciao.' })]);
  await page.goto('/chats');
  await expect(page.getByTestId('notification-badge')).toHaveText('2');

  await bell(page).click();
  const row = page.getByRole('region', { name: 'Notifiche' }).getByRole('listitem').filter({ hasText: 'Alba' });
  await row.getByRole('button', { name: 'Segna come letta' }).click();

  await expect(page.getByTestId('notification-badge')).toHaveText('1');
  await expect(row).not.toContainText('Non letta');

  await page.reload();
  await expect(page.getByTestId('notification-badge')).toHaveText('1');
});

test('read all clears the badge and the marks of the list', async ({ page }) => {
  await serveNotifications(page, [notice('n3'), notice('n2'), notice('n1')]);
  await page.goto('/chats');
  await expect(page.getByTestId('notification-badge')).toHaveText('3');

  await bell(page).click();
  await page.getByRole('button', { name: 'Segna tutte come lette' }).click();

  await expect(page.getByTestId('notification-badge')).toHaveCount(0);
  await expect(bell(page)).toHaveAccessibleName('Notifiche');
  const panel = page.getByRole('region', { name: 'Notifiche' });
  await expect(panel.getByRole('button', { name: 'Segna come letta' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Segna tutte come lette' })).toBeDisabled();
});
