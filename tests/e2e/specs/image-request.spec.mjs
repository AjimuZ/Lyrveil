// Image requests on the conversation screen and avatar generation in the Compose test project (P05.5b).
//   a manual request with the progress of both passes, the advanced dialog's limits, and a generated avatar.
//
// Since P05.7 the backend's worker runs an `image_request` in this project too, through the fake orchestrator's mode
// switch and the fake ComfyUI, so its timing depends on the mode switch. To keep each step checkable, the drawing is
// played by the browser's side of the connection: the real backend takes the request (the route, its checks, the
// placeholder message and the job), Playwright's WebSocket routing drops the real worker's image frames and delivers
// scripted `image.progress` and `message.completed` frames instead, and a route answers the job and asset reads that a
// finished generation would change. The backend half of the same flow, with the fake text model and the fake ComfyUI,
// is `backend/tests/integration/test_image_modes.py` (T-CHR-12), and the mode switch itself is
// `backend/tests/integration/test_image_mode_switch.py`.
import { expect, test } from '@playwright/test';

import { openChat, startChatWithNewCharacter, uniqueName } from '../support/chats.mjs';

// Injected events use ids far above the real ones, so the client's replay check (an event counts only if its id is
// newer than the last delivered) keeps accepting them in order.
let injectedId = 1_000_000_000;

/**
 * Tells whether a frame from the real backend reports image work: an `image.*` event, a message that ends as an image
 * or a mixed message, or a failed message (no text reply of these tests fails). The scripted frames replace them.
 */
function isRealImageFrame(message) {
  if (typeof message !== 'string') {
    return false;
  }
  let frame;
  try {
    frame = JSON.parse(message);
  } catch {
    return false;
  }
  const type = String(frame?.type ?? '');
  if (type.startsWith('image.') || type === 'message.failed') {
    return true;
  }
  return type === 'message.completed' && frame?.data?.message?.kind !== 'text';
}

/** Stands between the page and /ws, and returns a function that delivers one event frame to the page. */
async function routeSocket(page) {
  let toPage = null;
  await page.routeWebSocket(/\/ws$/, (socket) => {
    toPage = socket;
    const server = socket.connectToServer();
    socket.onMessage((message) => server.send(message));
    server.onMessage((message) => {
      if (!isRealImageFrame(message)) {
        socket.send(message);
      }
    });
  });
  return async (type, data) => {
    await expect.poll(() => toPage !== null).toBe(true);
    injectedId += 1;
    toPage.send(JSON.stringify({ v: 1, id: injectedId, type, ts: new Date().toISOString(), data }));
  };
}

async function openNewChat(page, base) {
  await page.goto('/chats');
  await expect(page.getByRole('heading', { level: 1, name: 'Conversazioni' })).toBeVisible();
  const name = uniqueName(base);
  await startChatWithNewCharacter(page, name);
  const id = await openChat(page, name);
  return { name, id };
}

/** The first image message of the chat, read through REST. */
async function imageMessage(page, conversationId) {
  const page1 = await (await page.request.get(`/api/v1/conversations/${conversationId}/messages`)).json();
  return page1.items.find((item) => item.kind === 'image');
}

test('a manual request shows the progress of both passes and then the image (T-IMG-07)', async ({ page }) => {
  const deliver = await routeSocket(page);
  const { id } = await openNewChat(page, 'Irene');

  await page.getByRole('button', { name: "Chiedi un'immagine" }).click();
  const dialog = page.getByRole('dialog', { name: "Chiedi un'immagine" });
  await dialog.getByLabel('Cosa vuoi vedere').fill('una foto di te seduta al porto');
  await dialog.getByLabel('Formato').selectOption('portrait');
  await dialog.getByLabel('Qualità').selectOption('alta');
  await dialog.getByRole('button', { name: 'Disegna' }).click();
  await expect(dialog).toBeHidden();

  // The request is the user's message, and the character's placeholder waits for the job.
  // Exact, because the closed dialog's hint also quotes this example in its longer text.
  await expect(page.getByText('una foto di te seduta al porto', { exact: true })).toBeVisible();
  await expect(page.getByText("In coda: l'immagine arriverà a breve.")).toBeVisible();
  const placeholder = await imageMessage(page, id);
  expect(placeholder.status).toBe('queued');

  const progress = page.getByRole('status', { name: "Avanzamento dell'immagine" });
  const send = (phase, value = null, max = null) =>
    deliver('image.progress', { job_id: 'job-1', message_id: placeholder.id, phase, value, max });

  await send('prompt');
  await expect(progress.getByTestId('image-phase')).toHaveText('Preparo la scena…');
  await expect(page.getByRole('status').filter({ hasText: 'Sta disegnando…' })).toBeVisible();

  await send('sampling', 14, 28);
  await expect(progress.getByTestId('image-phase')).toHaveText("Disegno l'immagine (prima passata)…");
  await expect(progress.getByTestId('image-step')).toHaveText('14 di 28');
  await expect(progress.getByRole('progressbar')).toHaveAttribute('value', '50');

  await send('refining', 5, 20);
  await expect(progress.getByTestId('image-phase')).toHaveText('Rifinisco i dettagli (seconda passata)…');
  await expect(progress.getByTestId('image-step')).toHaveText('5 di 20');
  await expect(progress.getByRole('progressbar')).toHaveAttribute('value', '25');

  await send('saving');
  await expect(progress.getByTestId('image-phase')).toHaveText("Salvo l'immagine…");

  const imageId = '0b1f6a52-3c1e-4c53-9d5e-9a4d1f2b7c10';
  await deliver('message.completed', {
    message: { ...placeholder, kind: 'mixed', status: 'complete', content: 'Eccola, il porto al tramonto.', image_id: imageId },
  });
  await expect(page.getByText('Eccola, il porto al tramonto.')).toBeVisible();
  await expect(progress).toHaveCount(0);
  // The thumbnail is served from /media, so it does not depend on ComfyUI (T-IMG-08). The file itself is not drawn in
  // this project, so only the address is checked.
  await expect(page.locator('li.message img.image')).toHaveAttribute('src', `/media/images/${imageId}/thumbnail`);
  await expect(page.getByRole('link', { name: "Apri l'immagine originale" })).toHaveAttribute(
    'href',
    `/media/images/${imageId}/original`,
  );
});

test("the advanced dialog refuses values outside its limits and sends the ones inside", async ({ page }) => {
  const { id } = await openNewChat(page, 'Giada');
  const requests = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith(`/conversations/${id}/images`)) {
      requests.push(request.postDataJSON());
    }
  });

  await page.getByRole('button', { name: "Chiedi un'immagine" }).click();
  const dialog = page.getByRole('dialog', { name: "Chiedi un'immagine" });

  // A blank request is refused before anything is sent.
  await dialog.getByRole('button', { name: 'Disegna' }).click();
  await expect(dialog.getByText('Scrivi cosa vuoi vedere.')).toBeVisible();

  await dialog.getByLabel('Cosa vuoi vedere').fill('un ritratto in biblioteca');
  await dialog.getByText('Impostazioni avanzate').click();
  await dialog.getByLabel('Seme (facoltativo)').fill('abc');
  await dialog.getByLabel('Passi (facoltativo, da 10 a 50)').fill('9');
  await dialog.getByLabel('CFG (facoltativo, da 1 a 12)').fill('13');
  await dialog.getByRole('button', { name: 'Disegna' }).click();
  await expect(dialog.getByText('Il seme è un numero intero da 0 a 4294967295.')).toBeVisible();
  await expect(dialog.getByText('I passi sono un numero intero da 10 a 50.')).toBeVisible();
  await expect(dialog.getByText('Il CFG è un numero da 1 a 12.')).toBeVisible();
  await expect(dialog).toBeVisible();
  expect(requests).toEqual([]);

  // Steps 51 and CFG 0.5 are still out of range; the bounds themselves are accepted.
  await dialog.getByLabel('Passi (facoltativo, da 10 a 50)').fill('51');
  await dialog.getByLabel('CFG (facoltativo, da 1 a 12)').fill('0.5');
  await dialog.getByRole('button', { name: 'Disegna' }).click();
  await expect(dialog.getByText('I passi sono un numero intero da 10 a 50.')).toBeVisible();
  await expect(dialog.getByText('Il CFG è un numero da 1 a 12.')).toBeVisible();
  expect(requests).toEqual([]);

  await dialog.getByLabel('Seme (facoltativo)').fill('4294967295');
  await dialog.getByLabel('Passi (facoltativo, da 10 a 50)').fill('50');
  await dialog.getByLabel('CFG (facoltativo, da 1 a 12)').fill('12');
  await dialog.getByLabel('Formato').selectOption('wide');
  await dialog.getByLabel('Qualità').selectOption('rapida');
  await dialog.getByRole('button', { name: 'Disegna' }).click();
  await expect(dialog).toBeHidden();
  expect(requests).toEqual([
    {
      request: 'un ritratto in biblioteca',
      aspect: 'wide',
      outfit: null,
      quality: 'rapida',
      seed: 4294967295,
      steps: 50,
      cfg: 12,
    },
  ]);
  // The real backend accepted the values and queued the request.
  await expect(page.getByText("In coda: l'immagine arriverà a breve.")).toBeVisible();
});

test('a generated avatar is followed through its job and shown as the primary one (T-CHR-12)', async ({ page }) => {
  const name = uniqueName('Selene');
  const created = await page.request.post('/api/v1/characters', {
    data: { identity: { first_name: name, age: 30, grammatical_gender: 'femminile' } },
  });
  expect(created.status()).toBe(201);
  const characterId = (await created.json()).id;

  // The request goes to the real backend, which queues the shared image job and answers 202 with it.
  let generateBody = null;
  let jobId = null;
  page.on('response', async (response) => {
    if (response.request().method() === 'POST' && response.url().endsWith(`/characters/${characterId}/assets/generate`)) {
      generateBody = response.request().postDataJSON();
      jobId = (await response.json()).job.id;
    }
  });

  // The job's reads are scripted, so each status shows regardless of how long the real job's mode switch takes: the
  // job runs its stages, then ends, and the avatar list changes.
  const generated = {
    id: '5d0c2a38-77a1-4a69-8f2e-3f0a6b7d9e11',
    character_id: characterId,
    kind: 'avatar',
    source: 'generated',
    media_type: 'image/png',
    width: 832,
    height: 1216,
    byte_size: 1000,
    is_primary: true,
    created_at: new Date().toISOString(),
    url: '/media/assets/5d0c2a38-77a1-4a69-8f2e-3f0a6b7d9e11',
    thumbnail_url: '/media/assets/5d0c2a38-77a1-4a69-8f2e-3f0a6b7d9e11/thumbnail',
  };
  const steps = [
    { status: 'queued', stage: 'prompt', queue_position: 1 },
    { status: 'running', stage: 'prompt', queue_position: 1 },
    { status: 'running', stage: 'render', queue_position: 1 },
    { status: 'completed', stage: 'persist', queue_position: 0 },
  ];
  let reads = 0;
  await page.route('**/api/v1/jobs/*', async (route) => {
    const step = steps[Math.min(reads, steps.length - 1)];
    reads += 1;
    await route.fulfill({
      json: {
        id: jobId,
        kind: 'image_request',
        attempt: 1,
        max_attempts: 3,
        error_code: null,
        error_detail: null,
        result: null,
        created_at: new Date().toISOString(),
        finished_at: null,
        ...step,
      },
    });
  });
  await page.route(`**/api/v1/characters/${characterId}/assets?kind=avatar`, async (route) => {
    await route.fulfill({ json: { items: reads >= steps.length ? [generated] : [] } });
  });

  await page.goto(`/characters/${characterId}`);
  await expect(page.getByText('Questo personaggio non ha ancora un avatar.')).toBeVisible();

  await page.getByRole('button', { name: 'Genera un avatar' }).click();
  const status = page.getByTestId('avatar-generation');
  // The first text comes from the real queue, whose position depends on the jobs earlier specs left waiting.
  // A regular expression is matched against the raw text, which the template pads with spaces.
  await expect(status).toHaveText(/^\s*In coda: posizione \d+\s*$/);
  await expect(status).toHaveText("Preparo la scena dell'avatar…", { timeout: 10_000 });
  await expect(status).toHaveText("Disegno l'avatar…", { timeout: 10_000 });

  expect(generateBody).toEqual({ purpose: 'avatar' });
  const variants = page.getByTestId('avatar-variants').getByRole('listitem');
  await expect(variants).toHaveCount(1);
  await expect(variants.first().getByText('Principale', { exact: true })).toBeVisible();
  await expect(variants.first().getByText('Generato', { exact: true })).toBeVisible();
  await expect(status).toHaveCount(0);
});
