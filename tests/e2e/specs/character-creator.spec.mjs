// The Character Creator of P03.2a (T-CHR-13): the full creation flow in the browser, through nginx and the real backend
// with the fake runtimes behind it. It creates a character with every card section, reloads, edits, saves, and checks
// the stored card through the API.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { uniqueName } from '../support/chats.mjs';

const tab = (page, name) => page.getByRole('button', { name: new RegExp(`^${name}`) });

async function expectNoAccessibilityViolations(page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) })),
  ).toEqual([]);
}

test('the new-chat dialog links to the full Creator', async ({ page }) => {
  await page.goto('/chats');
  await page.getByRole('button', { name: 'Nuova conversazione' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: 'Annulla' })).toBeVisible();
  const create = dialog.getByRole('button', { name: 'Nuovo personaggio' });
  const link = dialog.getByRole('link', { name: 'Apri il Creatore completo' });
  // The dialog can repaint under the click while the characters load, so the click is repeated until the form is there.
  await expect(async () => {
    if (!(await link.isVisible())) {
      await create.click({ timeout: 1000 });
    }
    await expect(link).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  await link.click();
  await expect(page).toHaveURL(/\/characters\/new$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Nuovo personaggio' })).toBeVisible();
});

test('a character is created with every section, stored, edited and left only after confirmation (T-CHR-13)', async ({
  page,
}) => {
  const name = uniqueName('Nerea');
  await page.goto('/characters/new');
  await expect(page.getByRole('heading', { level: 1, name: 'Nuovo personaggio' })).toBeVisible();
  await expect(page.getByLabel('Nome', { exact: true })).toBeVisible();

  // Identità: nothing is sent while the age is under 18, and the reason is the API's own, in Italian.
  await page.getByLabel('Nome', { exact: true }).fill(name);
  await page.getByLabel('Età', { exact: true }).fill('17');
  await page.getByRole('button', { name: 'Salva personaggio' }).click();
  await expect(page.getByText("L'età deve essere almeno 18.")).toBeVisible();
  await expect(page).toHaveURL(/\/characters\/new$/);
  await expectNoAccessibilityViolations(page);

  await page.getByLabel('Età', { exact: true }).fill('27');
  await page.getByLabel('Cognome (facoltativo)').fill('Falchi');
  await page.getByLabel('Come parla di sé').selectOption({ label: 'Al femminile' });
  await page.getByLabel('Occupazione').fill('Cartografa indipendente');
  await page.getByLabel('Biografia').fill('È cresciuta sopra una bottega di bussole.');
  await page.getByRole('button', { name: 'Aggiungi interesse' }).click();
  await page.getByLabel('Interesse 1').fill('mappe antiche');

  // Personalità: a trait, locked.
  await tab(page, 'Personalità').click();
  await page.getByRole('button', { name: 'Aggiungi tratto' }).click();
  await page.getByLabel('Categoria').selectOption({ label: 'Umorismo' });
  await page.getByLabel('Descrizione del tratto').fill('Ironia asciutta.');
  await page.getByRole('button', { name: 'Blocca tratto 1' }).click();
  await expect(page.getByRole('button', { name: 'Blocca tratto 1' })).toHaveAttribute('aria-pressed', 'true');

  // Comunicazione: a formal register proposes Lei, narration shows an example, then examples and phrases to avoid.
  await tab(page, 'Comunicazione').click();
  await page.getByLabel('Registro').selectOption({ label: 'Formale' });
  await expect(page.getByLabel('Forma di cortesia')).toHaveValue('lei');
  await page.getByLabel('Narrazione').selectOption({ label: 'Leggera' });
  await expect(page.getByText('Esempio: «Ciao, come stai?» *sorride*')).toBeVisible();
  await page.getByRole('button', { name: 'Aggiungi esempio' }).click();
  await page.getByLabel('Messaggio di chi scrive').fill('Dove sei stata?');
  await page.getByLabel('Risposta del personaggio').fill('In archivio, a rimettere in ordine il mondo.');
  await page.getByRole('button', { name: 'Aggiungi espressione' }).click();
  await page.getByLabel('Espressione 1').fill('per mari e per monti');
  await page.getByRole('button', { name: 'Aggiungi frase da evitare' }).click();
  await page.getByLabel('Frase da evitare 1').fill('Come modello linguistico');

  // Aspetto: Italian text with a lock, vocabulary slots found by their Italian words, the wardrobe.
  await tab(page, 'Aspetto').click();
  await page.getByLabel('Capelli', { exact: true }).fill('Capelli rossi, raccolti.');
  await page.getByRole('button', { name: 'Blocca: Capelli' }).click();
  await expect(page.getByRole('button', { name: 'Blocca: Capelli' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel('Soggetto')).toHaveValue('1girl');
  await page.getByLabel('Colore dei capelli').selectOption('red hair');
  await page.getByLabel('Tag del viso').fill('lentiggini');
  await page.getByRole('button', { name: /^freckles/ }).click();
  await expect(page.getByTestId('persistent-identity')).toHaveText('red hair, freckles');
  await page.getByLabel('Tag liberi').fill('brass pendant');
  await page.getByLabel('Tag liberi').press('Enter');
  await page.getByLabel('Abito abituale (tag)').fill('canvas jacket');
  await page.getByLabel('Abito abituale (tag)').press('Enter');

  await page.getByRole('button', { name: 'Aggiungi abito' }).click();
  await page.getByLabel("Chiave dell'abito").fill('usual');
  await expect(page.getByText('La chiave di un abito non può essere «usual» né «scene».')).toBeVisible();
  await page.getByLabel("Chiave dell'abito").fill('sera');
  await page.getByLabel("Nome dell'abito").fill('Sera');
  await page.getByLabel("Descrizione dell'abito").fill('Abito blu con nastro.');
  await page.getByLabel("Tag dell'abito").fill('blue dress');
  await page.getByLabel("Tag dell'abito").press('Enter');

  // Save: the character exists and the screen moves to its edit address.
  await page.getByRole('button', { name: 'Salva personaggio' }).click();
  await expect(page).toHaveURL(/\/characters\/[^/]+\/edit$/);
  const id = new URL(page.url()).pathname.split('/')[2];
  await expect(page.getByRole('heading', { level: 1, name: 'Modifica personaggio' })).toBeVisible();
  await expect(page.getByText('Personaggio creato.')).toBeVisible();

  const stored = await (await page.request.get(`/api/v1/characters/${id}`)).json();
  expect(stored.card.identity).toMatchObject({
    first_name: name,
    last_name: 'Falchi',
    age: 27,
    grammatical_gender: 'femminile',
    interests: ['mappe antiche'],
  });
  expect(stored.card.traits).toEqual([{ category: 'humor', description: 'Ironia asciutta.', locked: true }]);
  expect(stored.card.communication).toMatchObject({
    formality: 'formale',
    address_form: 'lei',
    narration: 'leggera',
    examples: [{ user: 'Dove sei stata?', character: 'In archivio, a rimettere in ordine il mondo.' }],
    recurring_expressions: ['per mari e per monti'],
    avoid: ['Come modello linguistico'],
  });
  expect(stored.card.visual).toMatchObject({
    hair: 'Capelli rossi, raccolti.',
    subject_tag: '1girl',
    locked_fields: ['hair'],
    usual_outfit_en: ['canvas jacket'],
    outfits: [{ key: 'sera', label: 'Sera', description: 'Abito blu con nastro.', tags_en: ['blue dress'] }],
  });
  expect(stored.card.visual.identity_en).toMatchObject({
    hair_color: 'red hair',
    face: ['freckles'],
    extra: ['brass pendant'],
  });

  // A reload shows what was stored.
  await page.reload();
  await expect(page.getByLabel('Nome', { exact: true })).toHaveValue(name);
  await expect(page.getByLabel('Età', { exact: true })).toHaveValue('27');
  await tab(page, 'Aspetto').click();
  await expect(page.getByLabel('Colore dei capelli')).toHaveValue('red hair');
  await expect(page.getByTestId('persistent-identity')).toHaveText('red hair, freckles, brass pendant');
  await expect(page.getByRole('button', { name: 'Blocca: Capelli' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel("Chiave dell'abito")).toHaveValue('sera');
  await expectNoAccessibilityViolations(page);

  // An edit adds a version, and a save with no change adds none.
  await tab(page, 'Identità').click();
  await page.getByLabel('Età', { exact: true }).fill('28');
  await page.getByRole('button', { name: 'Salva personaggio' }).click();
  await expect(page.getByText('Modifiche salvate. Versione 2.')).toBeVisible();
  await page.getByRole('button', { name: 'Salva personaggio' }).click();
  await expect(page.getByText('Nessuna modifica da salvare.')).toBeVisible();
  const versions = await (await page.request.get(`/api/v1/characters/${id}/versions`)).json();
  expect(versions.items.map((item) => item.version_no)).toEqual([2, 1]);

  // Leaving with unsaved changes asks first; refusing keeps the screen, accepting leaves it.
  await page.getByLabel('Occupazione').fill('Cartografa di corte');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.getByRole('link', { name: 'Torna alle conversazioni' }).click();
  await expect(page).toHaveURL(/\/characters\/[^/]+\/edit$/);
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('link', { name: 'Torna alle conversazioni' }).click();
  await expect(page).toHaveURL(/\/chats$/);
});
