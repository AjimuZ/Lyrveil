// T-QLT-03: accessibility of the ten screens of docs/frontend.md. `axe` runs on each screen, and keyboard walkthroughs
// check every shortcut of docs/frontend.md (Keyboard), the focus rules of docs/design-system.md (Accessibility: a
// visible ring, focus moved to the heading on a route change, dialogs that trap and return focus) and the live regions
// (the finished reply is announced once, the message log is not live itself).
//
// The characters and chats are set up through the API; the gallery runs over fake image routes (the same approach as
// gallery.spec.mjs), because how many real images exist depends on the specs that ran before.
import { expect, test } from '@playwright/test';

import { axeViolations } from '../support/axe.mjs';
import { uniqueName } from '../support/chats.mjs';

// A 1x1 PNG, enough for the browser to decode a thumbnail and an original.
const PIXEL = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

async function expectNoAccessibilityViolations(page) {
  expect(await axeViolations(page)).toEqual([]);
}

/** Creates a character and a chat with it through the API; returns their ids. */
async function createChat(page, name) {
  const created = await page.request.post('/api/v1/characters', {
    data: { identity: { first_name: name, age: 30, grammatical_gender: 'femminile' } },
  });
  expect(created.status()).toBe(201);
  const characterId = (await created.json()).id;
  const chat = await page.request.post('/api/v1/conversations', { data: { character_id: characterId } });
  expect(chat.status()).toBe(201);
  return { characterId, chatId: (await chat.json()).id };
}

/** Plays three gallery images over Playwright's routing; no ComfyUI is involved. */
async function fakeImages(page) {
  const rows = [1, 2, 3].map((n) => {
    const id = `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
    return {
      id,
      message_id: null,
      conversation_id: null,
      character_id: 'character-1',
      purpose: 'scene',
      width: 832,
      height: 1216,
      user_request: `foto numero ${n}`,
      aspect: 'portrait',
      outfit_key: 'usual',
      prompt_positive: `positive ${n}`,
      prompt_negative: 'negative',
      seed: 1000 + n,
      steps: 28,
      cfg: 5.5,
      profile_id: 'fake-profile',
      quality_preset: 'alta',
      workflow_id: 'txt2img-hires',
      scene: {},
      created_at: `2026-11-07T10:${String(59 - n).padStart(2, '0')}:00Z`,
      url: `/media/images/${id}/original`,
      thumbnail_url: `/media/images/${id}/thumbnail`,
    };
  });
  await page.route(/\/api\/v1\/images(\?.*)?$/, (route) => route.fulfill({ json: { items: rows, next_cursor: null } }));
  await page.route(/\/media\/images\/[^/]+\/(thumbnail|original)$/, (route) =>
    route.fulfill({ body: PIXEL, contentType: 'image/png' }),
  );
}

/** Waits until the screen has drawn its heading and finished its skeletons. */
async function screenReady(page) {
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
  await expect(page.locator('ob-skeleton')).toHaveCount(0);
}

test('axe finds no violation on the ten screens', async ({ page }) => {
  const name = uniqueName('Axe');
  const { characterId, chatId } = await createChat(page, name);
  await fakeImages(page);

  const screens = [
    ['the conversation list', '/chats'],
    ['the conversation', `/chats/${chatId}`],
    ['the Character Creator', '/characters/new'],
    ['the character profile', `/characters/${characterId}`],
    ['the Memory Studio', `/characters/${characterId}/memory`],
    ['the gallery', '/gallery'],
    ['the scenarios', '/scenarios'],
    ['the general settings', '/settings'],
    ['the model settings', '/settings/models'],
    ['the AI resources', '/settings/resources'],
  ];
  for (const [label, path] of screens) {
    await test.step(label, async () => {
      await page.goto(path);
      await screenReady(page);
      await expectNoAccessibilityViolations(page);
    });
  }
});

test('the skip link and a route change move focus, and the focus ring is drawn', async ({ page }) => {
  await page.goto('/gallery');
  await screenReady(page);

  // The first Tab stop is the skip link; it shows itself with the 2 px ring and takes the keyboard to the heading.
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Vai al contenuto' });
  await expect(skip).toBeFocused();
  const ring = await skip.evaluate((element) => {
    const style = getComputedStyle(element);
    return { style: style.outlineStyle, width: style.outlineWidth };
  });
  expect(ring).toEqual({ style: 'solid', width: '2px' });
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Galleria' })).toBeFocused();

  // Following a navigation link from the keyboard lands on the new screen's heading, not on a link that is gone.
  const scenarios = page.getByRole('navigation').getByRole('link', { name: 'Scenari', exact: true });
  await scenarios.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/scenarios$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Scenari' })).toBeFocused();
});

test('Ctrl+K, Ctrl+N and ? work from any screen, and the help dialog traps and returns focus', async ({ page }) => {
  await page.goto('/gallery');
  await screenReady(page);

  // Ctrl+K opens the conversation list with the search field ready.
  await page.keyboard.press('Control+k');
  await expect(page).toHaveURL(/\/chats$/);
  await expect(page.getByLabel('Cerca nelle conversazioni')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('Cerca nelle conversazioni')).not.toBeFocused();

  // Ctrl+N from another screen opens the new-conversation dialog; Esc closes it.
  await page.goto('/scenarios');
  await screenReady(page);
  await page.keyboard.press('Control+n');
  await expect(page).toHaveURL(/\/chats$/);
  const newChat = page.getByRole('dialog', { name: 'Nuova conversazione' });
  await expect(newChat).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(newChat).toBeHidden();

  // ? lists the shortcuts. Tab stays inside the dialog, Esc closes it, and focus returns to where it was.
  const trigger = page.getByRole('navigation').getByRole('link', { name: 'Galleria' });
  await trigger.focus();
  await page.keyboard.press('?');
  const help = page.getByRole('dialog', { name: 'Scorciatoie da tastiera' });
  await expect(help).toBeVisible();
  for (const keys of ['Ctrl+K', 'Ctrl+N', 'Alt+↑', 'Alt+↓', 'Ctrl+F', 'Invio', 'Maiusc+Invio', 'Esc']) {
    await expect(help.getByText(keys, { exact: true })).toBeVisible();
  }
  await expectNoAccessibilityViolations(page);
  for (let press = 0; press < 5; press += 1) {
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => document.activeElement?.closest('dialog') !== null)).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(help).toBeHidden();
  await expect(trigger).toBeFocused();
});

test('Alt+ArrowDown and Alt+ArrowUp step through the conversations of the list', async ({ page }) => {
  await createChat(page, uniqueName('Passo'));
  await createChat(page, uniqueName('Passo'));
  await page.goto('/chats');
  await expect(page.locator('a.chat').nth(1)).toBeVisible();
  const first = await page.locator('a.chat').nth(0).getAttribute('href');
  const second = await page.locator('a.chat').nth(1).getAttribute('href');

  await page.goto(first);
  await screenReady(page);
  await page.keyboard.press('Alt+ArrowDown');
  await expect(page).toHaveURL(new RegExp(`${second}$`));
  await expect(page.getByRole('heading', { level: 1 })).toBeFocused();
  await page.keyboard.press('Alt+ArrowUp');
  await expect(page).toHaveURL(new RegExp(`${first}$`));
});

test('the conversation: Enter and Shift+Enter, ArrowUp, Ctrl+F, Esc, and the live regions', async ({ page }) => {
  const name = uniqueName('Tasti');
  const { chatId } = await createChat(page, name);
  await page.goto(`/chats/${chatId}`);
  await screenReady(page);

  // The message list is a log that is not live by itself: streaming text is not read piece by piece.
  const log = page.getByRole('log');
  await expect(log).toHaveAttribute('aria-live', 'off');

  const composer = page.getByLabel('Scrivi un messaggio');
  await composer.focus();
  await composer.pressSequentially('prima');
  await page.keyboard.press('Shift+Enter');
  await composer.pressSequentially('seconda');
  await expect(composer).toHaveValue('prima\nseconda');
  await page.keyboard.press('Enter');
  await expect(composer).toHaveValue('');
  await expect(log.getByRole('listitem').filter({ hasText: 'prima' })).toBeVisible();

  // The finished reply is announced once, whole, through the polite region of the shell.
  const announcer = page.getByTestId('announcer');
  await expect(announcer).toContainText(`${name} ha risposto:`);

  // ArrowUp in the empty composer edits the latest message of the user; Esc leaves the editor for the composer.
  await composer.press('ArrowUp');
  const editor = page.getByLabel('Testo del messaggio');
  await expect(editor).toBeFocused();
  await expect(editor).toHaveValue('prima\nseconda');
  await page.keyboard.press('Escape');
  await expect(editor).toHaveCount(0);
  await expect(composer).toBeFocused();

  // Ctrl+F inside the chat opens the find bar, Enter steps, Esc closes it and returns to the composer.
  await page.keyboard.press('Control+f');
  const find = page.getByRole('search', { name: 'Cerca nella conversazione' });
  await expect(find).toBeVisible();
  const field = find.getByRole('searchbox');
  await expect(field).toBeFocused();
  await field.fill('seconda');
  await expect(find.getByTestId('find-count')).toHaveText('1 di 1');
  await expect(page.locator('li.message.current')).toContainText('seconda');
  await expectNoAccessibilityViolations(page);
  await page.keyboard.press('Escape');
  await expect(find).toHaveCount(0);
  await expect(composer).toBeFocused();
});

test('← and → move between the images of the lightbox, and Esc returns focus to the thumbnail', async ({ page }) => {
  await fakeImages(page);
  await page.goto('/gallery');
  await screenReady(page);

  const grid = page.getByRole('list', { name: 'Immagini' });
  const thumbnail = grid.getByRole('button', { name: /foto numero 1$/ });
  await thumbnail.click();
  const lightbox = page.getByRole('dialog', { name: 'Immagine ingrandita' });
  await expect(lightbox).toBeVisible();
  await expect(lightbox.getByTestId('lightbox-position')).toHaveText('Immagine 1 di 3');
  await expect(lightbox.getByTestId('gallery-seed')).toHaveText('1001');

  await page.keyboard.press('ArrowRight');
  await expect(lightbox.getByTestId('lightbox-position')).toHaveText('Immagine 2 di 3');
  await expect(lightbox.getByTestId('gallery-seed')).toHaveText('1002');
  await page.keyboard.press('ArrowLeft');
  await expect(lightbox.getByTestId('gallery-seed')).toHaveText('1001');
  // At the first image ← does nothing.
  await page.keyboard.press('ArrowLeft');
  await expect(lightbox.getByTestId('lightbox-position')).toHaveText('Immagine 1 di 3');

  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
  await expect(thumbnail).toBeFocused();
});
