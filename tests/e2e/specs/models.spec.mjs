// P06.6b: the model settings screen, through nginx and the real backend with the fake runtimes behind it. The models
// volume of compose.test.yaml holds a sparse file of the catalog's size for every profile, so each one reads as
// verified, and one small file named like a custom model for the registration.
//
// All tests share one project, so the test puts the active text profile back before it ends.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const CUSTOM_FILE = 'e2e-custom.Q4_K_M.gguf';

function card(page, name) {
  return page.getByRole('listitem').filter({ has: page.getByRole('heading', { name, exact: true }) });
}

test('the profiles show tier, licence and state, a profile is chosen and applied, and a custom file is registered', async ({
  page,
}) => {
  const settings = await (await page.request.get('/api/v1/settings/models')).json();
  const original = settings.text.find((profile) => profile.id === settings.active.text);
  const other = settings.text.find((profile) => profile.selectable && profile.id !== settings.active.text);
  expect(other, 'the machine tier allows a second text profile').toBeDefined();

  await page.goto('/settings/models');
  await expect(page.getByRole('heading', { level: 1, name: 'Modelli' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Modello di testo' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Modello per immagini' })).toBeVisible();

  // Every catalog profile is listed with its licence, its minimum tier and the state of its local file.
  for (const profile of [...settings.text, ...settings.image]) {
    const row = card(page, profile.display_name);
    await expect(row).toBeVisible();
    await expect(row).toContainText(`Licenza ${profile.license}`);
    await expect(row).toContainText(profile.min_tier === 'target' ? 'Livello minimo: Completo' : 'Livello minimo: Ridotto');
    await expect(row).toContainText('Verificato');
  }
  await expect(card(page, original.display_name)).toContainText('In uso');
  await expect(page.getByRole('button', { name: 'Applica ora' })).toHaveCount(0);

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);

  // Choosing a profile writes the launch file and offers Applica ora, which reloads the model.
  await card(page, other.display_name).getByRole('button', { name: 'Usa questo modello' }).click();
  await expect(card(page, other.display_name)).toContainText('In uso');
  const chosen = await (await page.request.get('/api/v1/settings/models')).json();
  expect(chosen.active.text).toBe(other.id);
  expect(chosen.launch.MODEL_PROFILE).toBe(other.id);
  await page.getByRole('button', { name: 'Applica ora' }).click();
  await expect(page.getByText('Ricarica avviata: il nuovo modello si sta caricando.')).toBeVisible({ timeout: 90_000 });

  // A file already in the models folder is registered by name; it stays marked as not verified.
  const form = page.getByRole('form', { name: 'Aggiungi file locale' });
  await expect(form.getByLabel('File')).toHaveValue(CUSTOM_FILE);
  await form.getByLabel('Formato del prompt').selectOption({ label: 'Formato ChatML' });
  await form.getByRole('button', { name: 'Registra file' }).click();
  await expect(page.getByText('File registrato. Non è verificato dal catalogo.')).toBeVisible({ timeout: 60_000 });
  const custom = card(page, CUSTOM_FILE);
  await expect(custom).toContainText('non verificato');
  await expect(custom.getByRole('button', { name: 'Usa questo modello' })).toBeEnabled();

  // Removing the registration keeps the file, which is offered for registration again.
  await custom.getByRole('button', { name: 'Rimuovi' }).click();
  await custom.getByRole('button', { name: "Rimuovi dall'elenco" }).click();
  await expect(card(page, CUSTOM_FILE)).toHaveCount(0);
  await expect(form.getByLabel('File')).toHaveValue(CUSTOM_FILE);

  // Put the original profile back, so the other specs run on the default.
  await card(page, original.display_name).getByRole('button', { name: 'Usa questo modello' }).click();
  await expect(card(page, original.display_name)).toContainText('In uso');
});

// The tier of the e2e machine is whatever the host reports, so this test rewrites the response of the settings call to
// a reduced machine with one missing file. That makes the "above the tier" and "to download" cases always present,
// instead of depending on the host; the real backend still answers every other call.
test('a profile above the machine tier or without its file cannot be chosen and shows how to get it', async ({ page }) => {
  const command = '.\\scripts\\lyrveil.cmd models -Model e2e';
  await page.route('**/api/v1/settings/models', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const body = await response.json();
    body.tier = 'reduced';
    const active = body.active.text;
    body.text = body.text.map((profile) => {
      if (profile.id === active) {
        return profile;
      }
      return profile.min_tier === 'target'
        ? { ...profile, selectable: false }
        : { ...profile, state: 'missing', selectable: true, download_command: command };
    });
    await route.fulfill({ response, json: body });
  });

  const settings = await (await page.request.get('/api/v1/settings/models')).json();
  const above = settings.text.find((profile) => profile.min_tier === 'target' && profile.id !== settings.active.text);
  const missing = settings.text.find((profile) => profile.min_tier === 'reduced' && profile.id !== settings.active.text);
  expect(above, 'the catalog has a full-tier text profile that is not active').toBeDefined();
  expect(missing, 'the catalog has a reduced-tier text profile that is not active').toBeDefined();

  await page.goto('/settings/models');
  const aboveRow = card(page, above.display_name);
  await expect(aboveRow).toContainText('Serve un computer di livello Completo');
  await expect(aboveRow.getByRole('button', { name: 'Usa questo modello' })).toBeDisabled();

  const missingRow = card(page, missing.display_name);
  await expect(missingRow).toContainText('Da scaricare');
  await expect(missingRow).toContainText(command);
});
