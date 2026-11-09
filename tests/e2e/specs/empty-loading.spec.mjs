// Loading and empty states of the list screens (P08.1b): a skeleton with a status text while a list loads, then the
// empty state with its sentence. The lists are answered by the test so that the state is certain whatever the shared
// database holds; the delay keeps the loading state on screen long enough to look at.
import { expect, test } from '@playwright/test';

const delayMs = 1_200;

/** Answers `pattern` with `body` after a delay, so the loading state is observable. */
async function answerSlowly(page, pattern, body) {
  await page.route(pattern, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test('the chat list shows a skeleton while loading, then its empty state', async ({ page }) => {
  await answerSlowly(page, /\/api\/v1\/conversations(\?.*)?$/, { items: [] });
  await page.goto('/chats');
  await expect(page.getByRole('status').filter({ hasText: 'Caricamento delle conversazioni…' })).toBeAttached();
  await expect(page.locator('ob-skeleton')).toBeVisible();
  await expect(page.locator('ob-empty-state')).toContainText('Non hai ancora conversazioni.');
  await expect(page.locator('ob-skeleton')).toHaveCount(0);
});

test('the gallery shows a skeleton while loading, then its empty state', async ({ page }) => {
  await answerSlowly(page, /\/api\/v1\/images(\?.*)?$/, { items: [], next_cursor: null });
  await page.goto('/gallery');
  await expect(page.getByRole('status').filter({ hasText: 'Carico le immagini…' })).toBeAttached();
  await expect(page.locator('ob-skeleton')).toBeVisible();
  await expect(page.locator('ob-empty-state')).toContainText('Nessuna immagine.');
});

test('reduced motion removes animation and transition durations', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/chats');
  await expect(page.getByRole('heading', { level: 1, name: 'Conversazioni' })).toBeVisible();
  const durations = await page.evaluate(() => {
    const style = getComputedStyle(document.body);
    return [style.animationDuration, style.transitionDuration];
  });
  expect(durations).toEqual(['0s', '0s']);
});
