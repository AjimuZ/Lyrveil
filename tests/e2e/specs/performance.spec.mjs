// Performance of the long lists (P08.4): a conversation of 10,000 messages and a gallery of 1,000 images.
//
// Both play the routes with Playwright's routing over a synthetic data set, because the numbers are the point: the real
// stack would need ten thousand messages written first, and its answers are covered by the backend tests. The page code
// is the real one. Each test records its timings as an attachment (`timings`) and in the list reporter; the limits are
// generous on purpose, since they guard against a list that renders or loads everything (seconds, not milliseconds),
// not against a slower machine.
import { expect, test } from '@playwright/test';

import { fakeImages } from '../support/images.mjs';

const MESSAGES = 10_000;
const PAGE_SIZE = 50;
const CHAT = 'perf-chat';
/** The most message and day rows the virtual list may keep in the DOM: the viewport plus the overscan, with room. */
const MAX_DRAWN_ROWS = 150;

function message(seq) {
  const day = new Date(Date.UTC(2025, 0, 1) + Math.floor(seq / 20) * 86_400_000);
  const moment = new Date(day.getTime() + (seq % 20) * 60_000).toISOString();
  return {
    id: `message-${seq}`,
    conversation_id: CHAT,
    seq,
    author: seq % 2 === 1 ? 'user' : 'character',
    kind: 'text',
    status: 'complete',
    read_state: seq % 2 === 1 ? 'read' : null,
    active_variant_id: null,
    content: `Messaggio ${seq}`,
    variant_count: 1,
    edited_at: null,
    created_at: moment,
  };
}

/** Plays the conversation routes for `MESSAGES` messages; returns the `before_seq` of every page asked for. */
async function fakeConversation(page) {
  const seen = { pages: [] };
  const conversation = {
    id: CHAT,
    character_id: 'character-1',
    character_name: 'Giulia',
    title: 'Giulia',
    language: null,
    effective_language: 'it',
    status: 'active',
    pinned: false,
    pinned_at: null,
    shares_memory: false,
    unread_count: 0,
    last_message_at: message(MESSAGES).created_at,
    created_at: message(1).created_at,
    updated_at: message(MESSAGES).created_at,
  };
  await page.route(new RegExp(`/api/v1/conversations/${CHAT}$`), (route) => route.fulfill({ json: conversation }));
  await page.route(new RegExp(`/api/v1/conversations/${CHAT}/read$`), (route) => route.fulfill({ json: conversation }));
  await page.route(new RegExp(`/api/v1/conversations/${CHAT}/messages(\\?.*)?$`), (route) => {
    const url = new URL(route.request().url());
    const before = url.searchParams.has('before_seq') ? Number(url.searchParams.get('before_seq')) : MESSAGES + 1;
    const limit = Number(url.searchParams.get('limit') ?? PAGE_SIZE);
    const first = Math.max(1, before - limit);
    seen.pages.push(before);
    const items = Array.from({ length: before - first }, (_, index) => message(first + index));
    return route.fulfill({ json: { items, next_before_seq: first > 1 ? first : null } });
  });
  return seen;
}

/** Message and day rows currently in the DOM. */
const drawnRows = (page) => page.locator('ol[aria-label="Messaggi"] > li[data-row]').count();

test('a conversation of 10,000 messages opens, pages and scrolls with a bounded DOM', async ({ page }, testInfo) => {
  test.setTimeout(300_000);
  const seen = await fakeConversation(page);
  const timings = {};
  const scroller = page.locator('.scroller');

  // Opening reads one page, not the history, and shows the newest message.
  const opened = Date.now();
  await page.goto(`/chats/${CHAT}`);
  await expect(page.getByText(`Messaggio ${MESSAGES}`, { exact: true })).toBeVisible();
  timings.openMs = Date.now() - opened;
  expect(seen.pages).toEqual([MESSAGES + 1]);
  expect(timings.openMs).toBeLessThan(15_000);

  // Scrolling to the top asks for older pages one by one, by their cursor, until the oldest message is loaded. Past a
  // few hundred rows the list is virtual, so the DOM stays small however much history has been loaded.
  const pageMs = [];
  let widest = await drawnRows(page);
  const pagesToLoad = MESSAGES / PAGE_SIZE - 1;
  for (let loaded = 1; loaded <= pagesToLoad; loaded += 1) {
    const requested = Date.now();
    await expect(async () => {
      await scroller.evaluate((element) => {
        element.scrollTop = 0;
      });
      expect(seen.pages.length).toBeGreaterThan(loaded);
    }).toPass({ timeout: 15_000, intervals: [50, 100, 250] });
    pageMs.push(Date.now() - requested);
    if (loaded % 20 === 0) {
      widest = Math.max(widest, await drawnRows(page));
    }
  }
  expect(seen.pages).toHaveLength(pagesToLoad + 1);
  expect(seen.pages.slice(0, 3)).toEqual([MESSAGES + 1, MESSAGES - PAGE_SIZE + 1, MESSAGES - 2 * PAGE_SIZE + 1]);
  timings.pages = pageMs.length;
  timings.pageMeanMs = Math.round(pageMs.reduce((sum, value) => sum + value, 0) / pageMs.length);
  timings.pageMaxMs = Math.max(...pageMs);

  // At the top the oldest message is on screen, nothing more is offered, and the DOM is still small. The last page put
  // the reader back where they were, so the list is taken to the top once more.
  await expect(async () => {
    await scroller.evaluate((element) => {
      element.scrollTop = 0;
    });
    await expect(page.getByText('Messaggio 1', { exact: true })).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Mostra messaggi precedenti' })).toHaveCount(0);
  widest = Math.max(widest, await drawnRows(page));
  timings.widestDrawnRows = widest;
  expect(widest).toBeLessThan(MAX_DRAWN_ROWS);

  // Scrolling through the loaded history: frames stay short, and the DOM stays bounded while the window moves.
  const frames = await scroller.evaluate(async (element) => {
    const durations = [];
    let last = performance.now();
    for (let step = 0; step < 80; step += 1) {
      element.scrollTop += 900;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const now = performance.now();
      durations.push(now - last);
      last = now;
    }
    return durations;
  });
  timings.scrollFrameMeanMs = Math.round(frames.reduce((sum, value) => sum + value, 0) / frames.length);
  timings.scrollFrameMaxMs = Math.round(Math.max(...frames));
  expect(timings.scrollFrameMeanMs).toBeLessThan(100);
  expect(await drawnRows(page)).toBeLessThan(MAX_DRAWN_ROWS);
  await expect(page.locator('li.message').first()).toBeVisible();

  // Jumping back to the newest messages draws them again.
  const jumped = Date.now();
  await expect(async () => {
    await scroller.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(page.getByText(`Messaggio ${MESSAGES}`, { exact: true })).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  timings.jumpToNewestMs = Date.now() - jumped;
  expect(await drawnRows(page)).toBeLessThan(MAX_DRAWN_ROWS);

  console.log(`conversation of ${MESSAGES} messages: ${JSON.stringify(timings)}`);
  await testInfo.attach('timings', { body: JSON.stringify(timings, null, 2), contentType: 'application/json' });
});

test('a gallery of 1,000 images loads its thumbnails lazily', async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const TOTAL = 1000;
  const seen = await fakeImages(page, { total: TOTAL });
  const timings = {};

  const opened = Date.now();
  await page.goto('/gallery');
  const grid = page.getByRole('list', { name: 'Immagini' });
  await expect(grid.getByRole('listitem')).toHaveCount(24);
  timings.firstPageMs = Date.now() - opened;

  // Every page is loaded without scrolling, so the thumbnails the browser fetched are the ones near the viewport.
  const more = page.getByRole('button', { name: 'Carica altre' });
  const loading = Date.now();
  for (let count = 24; count < TOTAL; count += 24) {
    const target = Math.min(count + 24, TOTAL);
    // The click is a script click, so the page does not scroll; it does nothing while the button is disabled.
    await expect(async () => {
      if ((await grid.getByRole('listitem').count()) < target) {
        await more.evaluate((button) => button.click());
      }
      await expect(grid.getByRole('listitem')).toHaveCount(target, { timeout: 500 });
    }).toPass({ timeout: 10_000 });
  }
  timings.allPagesMs = Date.now() - loading;
  await expect(more).toHaveCount(0);
  expect(seen.listings).toHaveLength(Math.ceil(TOTAL / 24));
  expect(seen.originals).toBe(0);

  // The list holds all 1,000 images, but the browser fetched the thumbnails near the viewport, not all of them.
  await page.waitForTimeout(500);
  const fetchedAtTop = seen.thumbnails;
  timings.thumbnailsFetchedAtTop = fetchedAtTop;
  expect(fetchedAtTop).toBeGreaterThan(0);
  expect(fetchedAtTop).toBeLessThan(TOTAL / 4);

  // Scrolling to the end fetches the thumbnails there, and still not all of them.
  const last = grid.getByRole('listitem').last();
  await last.scrollIntoViewIfNeeded();
  await expect
    .poll(() => last.locator('img').evaluate((image) => image.complete && image.naturalWidth > 0), { timeout: 15_000 })
    .toBe(true);
  expect(seen.thumbnails).toBeGreaterThan(fetchedAtTop);
  expect(seen.thumbnails).toBeLessThan(TOTAL);
  timings.thumbnailsFetchedAtEnd = seen.thumbnails;

  console.log(`gallery of ${TOTAL} images: ${JSON.stringify(timings)}`);
  await testInfo.attach('timings', { body: JSON.stringify(timings, null, 2), contentType: 'application/json' });
});
