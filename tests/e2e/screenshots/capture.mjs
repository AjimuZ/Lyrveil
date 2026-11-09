// Regenerates the README screenshots (R-PUB-05) into docs/images/screenshots/ from the Compose test project.
//
// Only the original demo characters (POST /api/v1/characters/demo) and synthetic data appear: the gallery plays the
// fake image routes of `support/images.mjs`, and the conversation is written by the test runtime's fakes. Run it with
// the test project up: `node screenshots/capture.mjs` from tests/e2e (LYRVEIL_TEST_HTTP_PORT overrides the port).
// Every shot is checked against a short list of strings that must never appear (home paths, tokens, e-mail addresses).
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from '@playwright/test';

import { fakeImages } from '../support/images.mjs';

const port = process.env.LYRVEIL_TEST_HTTP_PORT || '14200';
const baseURL = `http://127.0.0.1:${port}`;
const outDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'docs', 'images', 'screenshots');

/** Text that would reveal a real person or machine; a screen showing any of it is not saved. */
const forbidden = [/[A-Za-z]:\\Users\\/i, /\/home\/[a-z]/i, /\/Users\/[A-Za-z]/, /[\w.+-]+@[\w-]+\.[a-z]{2,}/i, /\b(?:sk|ghp|hf)_[A-Za-z0-9]{10,}/];

const context = { viewport: { width: 1440, height: 900 }, locale: 'it-IT', baseURL };

/** Saves a screenshot of the current page after checking its text against `forbidden`. */
async function shoot(page, name) {
  const text = await page.evaluate(() => document.body.innerText);
  for (const pattern of forbidden) {
    if (pattern.test(text)) {
      throw new Error(`${name}: the screen shows text that matches ${pattern}`);
    }
  }
  await page.screenshot({ path: join(outDir, `${name}.png`) });
  console.log(`saved ${name}.png`);
}

await mkdir(outDir, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await (await browser.newContext(context)).newPage();

  // Demo characters: loaded on request, skipping the ones that already exist.
  const loaded = await page.request.post(`${baseURL}/api/v1/characters/demo`);
  if (!loaded.ok()) {
    throw new Error(`the demo characters did not load: HTTP ${loaded.status()}`);
  }
  const { created } = await loaded.json();
  const characters = (await (await page.request.get(`${baseURL}/api/v1/characters`)).json()).items;
  const nerea = characters.find((item) => item.is_demo && item.first_name === 'Nerea');
  if (nerea === undefined) {
    throw new Error(`the demo character Nerea is missing (${created.length} created)`);
  }

  await page.goto('/chats');
  await page.getByRole('heading', { level: 1 }).waitFor();
  await shoot(page, 'chats');

  await page.goto(`/characters/${nerea.id}`);
  await page.getByRole('heading', { level: 1 }).waitFor();
  await shoot(page, 'character-profile');

  await page.goto('/scenarios');
  await page.getByRole('heading', { level: 1 }).waitFor();
  await shoot(page, 'scenarios');

  await page.goto('/settings/models');
  await page.getByRole('heading', { level: 1 }).waitFor();
  await shoot(page, 'models');

  // The gallery uses the synthetic image fake: 1 x 1 pixel images under made-up requests.
  await fakeImages(page, { total: 12 });
  await page.goto('/gallery');
  await page.getByRole('list', { name: 'Immagini' }).waitFor();
  await shoot(page, 'gallery');
} finally {
  await browser.close();
}
