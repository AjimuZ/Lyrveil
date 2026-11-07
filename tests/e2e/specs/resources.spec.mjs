// P06.6b: the AI resources screen, through nginx and the real backend, orchestrator and fake NVML of compose.test.yaml.
// The live update on a `gpu.state` event is covered by the store's unit tests, since the fake runtimes move too fast
// between states for a browser to watch.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('the screen shows the GPU state, the card memory, the queue and the active model with its launch level', async ({
  page,
}) => {
  const models = await (await page.request.get('/api/v1/settings/models')).json();
  const active = models.text.find((profile) => profile.id === models.active.text);

  await page.goto('/settings/resources');
  await expect(page.getByRole('heading', { level: 1, name: 'Risorse AI' })).toBeVisible();

  // The state shows in words and as the orchestrator's code.
  const gpu = page.getByRole('region', { name: 'Scheda video' });
  await expect(gpu.locator('code')).toHaveText(
    /^(IDLE|LOADING_LLM|LLM_READY|GENERATING_TEXT|UNLOADING_LLM|LOADING_IMAGE|IMAGE_READY|GENERATING_IMAGE|UNLOADING_IMAGE|RECOVERING|ERROR)$/,
  );

  // The fake NVML reports a 12282 MiB card; the gauge and its numbers come from the orchestrator's reading.
  const vram = page.getByRole('region', { name: 'Memoria video (VRAM)' });
  await expect(vram).toContainText('12.282 MiB');
  await expect(vram).toContainText('MiB liberi');
  await expect(vram.getByRole('meter', { name: 'Memoria video in uso' })).toBeVisible();

  const queue = page.getByRole('region', { name: 'Coda' });
  await expect(queue).toContainText('in coda');
  await expect(queue.getByRole('listitem')).toHaveCount(3);

  const model = page.getByRole('region', { name: 'Modello attivo' });
  await expect(model).toContainText(active.display_name);
  await expect(model).toContainText('Livello di avvio');

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
});

test('the navigation reaches the model settings and the resources', async ({ page }) => {
  await page.goto('/chats');
  await page.getByRole('link', { name: 'Modelli' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Modelli' })).toBeVisible();
  await page.getByRole('link', { name: 'Risorse AI' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Risorse AI' })).toBeVisible();
});
