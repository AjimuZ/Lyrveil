// T-QLT-04: the layouts of docs/design-system.md (Layout) at 320, 640, 1024 and 1440 px and at 200 % zoom. At every
// size no screen scrolls sideways and no control sticks out of the viewport; each breakpoint shows its own layout.
// 200 % zoom is checked the way a browser applies it: the CSS viewport is half as wide and half as tall.
import { expect, test } from '@playwright/test';

import { openChat, sendText, startChatWithNewCharacter, uniqueName } from '../support/chats.mjs';

/** The sizes of the plan: width, height and a name. */
const sizes = [
  { name: '320 px', width: 320, height: 640 },
  { name: '640 px', width: 640, height: 800 },
  { name: '1024 px', width: 1024, height: 768 },
  { name: '1440 px', width: 1440, height: 900 },
  { name: '200 % zoom of 1440 px', width: 720, height: 450 },
  { name: '200 % zoom of 640 px', width: 320, height: 400 },
];

/** Screens that need no data: the lists show their empty or loaded state, the forms their fields. */
const screens = [
  '/chats',
  '/gallery',
  '/scenarios',
  '/characters/new',
  '/settings',
  '/settings/models',
  '/settings/resources',
];

/**
 * Returns what sticks out of the viewport: the page's own scroll width against its client width, the scroll width of
 * the screen area, and every visible control whose box leaves the viewport sideways. A control inside a region that
 * scrolls on its own (a code block, say) is allowed to be wider than its region, but not to start outside the screen.
 */
async function overflow(page) {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const content = document.querySelector('main.content');
    const sticking = [];
    const controls = document.querySelectorAll('a[href], button, input, select, textarea, summary, [role="button"]');
    for (const element of controls) {
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0 || getComputedStyle(element).visibility === 'hidden') {
        continue;
      }
      // A visually hidden control (a one-pixel clip) is not drawn, so it cannot be clipped.
      if (box.width <= 1 && box.height <= 1) {
        continue;
      }
      if (box.left < -1 || box.right > width + 1) {
        sticking.push(`${element.tagName.toLowerCase()} ${(element.textContent ?? '').trim().slice(0, 30)}`);
      }
    }
    return {
      pageScroll: document.documentElement.scrollWidth - width,
      contentScroll: content === null ? 0 : content.scrollWidth - content.clientWidth,
      sticking,
    };
  });
}

/** Asserts that nothing scrolls sideways or leaves the viewport. */
async function expectNoSidewaysOverflow(page) {
  const found = await overflow(page);
  expect(found.pageScroll, 'the page scrolls sideways').toBeLessThanOrEqual(0);
  expect(found.contentScroll, 'the screen area scrolls sideways').toBeLessThanOrEqual(0);
  expect(found.sticking, 'controls outside the viewport').toEqual([]);
}

for (const size of sizes) {
  test.describe(`at ${size.name}`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });

    for (const path of screens) {
      test(`${path} has no sideways scroll and no clipped control`, async ({ page }) => {
        await page.goto(path);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        await expectNoSidewaysOverflow(page);
      });
    }

    test('the shell has the layout of its breakpoint', async ({ page }) => {
      await page.goto('/chats');
      const nav = page.getByRole('navigation');
      await expect(nav).toBeVisible();
      const navBox = await nav.boundingBox();
      const mainBox = await page.locator('main.content').boundingBox();
      expect(navBox).not.toBeNull();
      expect(mainBox).not.toBeNull();
      if (size.width < 640) {
        // Mobile: the navigation is a bar under the screen, across its whole width.
        expect(navBox.y).toBeGreaterThanOrEqual(mainBox.y + mainBox.height - 1);
        expect(navBox.width).toBeGreaterThanOrEqual(size.width - 1);
        for (const link of await nav.getByRole('link').all()) {
          const box = await link.boundingBox();
          expect(box.height).toBeGreaterThanOrEqual(44);
        }
      } else {
        // Tablet and desktop: the navigation is a sidebar on the left of the screen, 320 px from 1024 px up.
        expect(navBox.x).toBeLessThan(mainBox.x);
        expect(navBox.y).toBeLessThan(mainBox.y + mainBox.height);
        if (size.width >= 1024) {
          expect(Math.round(navBox.width)).toBe(320);
        } else {
          expect(navBox.width).toBeGreaterThanOrEqual(200);
          expect(navBox.width).toBeLessThanOrEqual(320);
        }
      }
    });
  });
}

test.describe('the conversation', () => {
  test.describe.configure({ mode: 'serial' });

  let conversationPath = '';
  const characterName = uniqueName('Resp');

  test('is created for the checks below', async ({ page }) => {
    await page.goto('/chats');
    await startChatWithNewCharacter(page, characterName);
    await openChat(page, characterName);
    conversationPath = new URL(page.url()).pathname;
    await sendText(page, 'Una parola lunghissima: ' + 'a'.repeat(120));
    await expect(page.getByRole('listitem').filter({ hasText: 'Una parola lunghissima' })).toBeVisible();
  });

  for (const size of sizes) {
    test.describe(`at ${size.name}`, () => {
      test.use({ viewport: { width: size.width, height: size.height } });

      test('has no sideways scroll, a visible composer and the layout of its breakpoint', async ({ page }) => {
        await page.goto(conversationPath);
        await expect(page.getByRole('heading', { level: 1, name: characterName })).toBeVisible();
        await expectNoSidewaysOverflow(page);

        const composer = page.getByLabel('Scrivi un messaggio');
        await expect(composer).toBeVisible();
        // The composer is at the bottom of the visible area: it is inside the viewport vertically.
        const composerBox = await composer.boundingBox();
        expect(composerBox.y + composerBox.height).toBeLessThanOrEqual(size.height + 1);

        const nav = page.getByRole('navigation');
        const context = page.locator('aside#context-panel');
        const details = page.getByRole('button', { name: 'Dettagli', exact: true });
        if (size.width >= 1024) {
          await expect(nav).toBeVisible();
          await expect(context).toBeVisible();
          expect(Math.round((await context.boundingBox()).width)).toBe(320);
          // The panel collapses and comes back.
          await details.click();
          await expect(context).toBeHidden();
          await details.click();
          await expect(context).toBeVisible();
        } else {
          await expect(context).toHaveCount(0);
          await details.click();
          const dialog = page.getByRole('dialog', { name: 'Dettagli della conversazione' });
          await expect(dialog).toBeVisible();
          await expect(dialog.getByRole('link', { name: 'Apri il profilo' })).toBeVisible();
          await dialog.getByRole('button', { name: 'Chiudi i dettagli' }).click();
          await expect(dialog).toBeHidden();
        }

        if (size.width < 640) {
          // Mobile: no shell navigation around the conversation, and a tappable composer row.
          await expect(nav).toBeHidden();
          for (const name of ["Chiedi un'immagine", 'Invia']) {
            const box = await page.getByRole('button', { name }).boundingBox();
            expect(box.width).toBeGreaterThanOrEqual(44);
            expect(box.height).toBeGreaterThanOrEqual(44);
            // Image button, field and send button share one row.
            expect(Math.abs(box.y + box.height - (composerBox.y + composerBox.height))).toBeLessThanOrEqual(12);
          }
        } else {
          await expect(nav).toBeVisible();
        }
      });
    });
  }

  test('keeps messages within 760 px on a wide screen', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(conversationPath);
    const list = page.getByRole('list', { name: 'Messaggi' });
    await expect(list).toBeVisible();
    expect((await list.boundingBox()).width).toBeLessThanOrEqual(760);
  });

  test('the chat list is separate from the conversation on a phone', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await page.goto('/chats');
    await expect(page.getByRole('navigation')).toBeVisible();
    await expect(page.getByLabel('Scrivi un messaggio')).toHaveCount(0);
    await page.goto(conversationPath);
    await expect(page.getByLabel('Scrivi un messaggio')).toBeVisible();
    await expect(page.getByRole('navigation')).toBeHidden();
  });
});
