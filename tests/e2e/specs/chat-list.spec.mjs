// The conversation list of P02.6a: the minimal character form and a new chat (T-MSG-01), then pin, archive, delete,
// restore and search (T-MSG-11), through nginx and the real backend with the fake runtimes behind it.
//
// All tests share one database, so each test creates characters with names unique to the run and looks only at them.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const run = Date.now().toString(36);
let counter = 0;

/** Returns a first name no other test or run uses, made of letters and digits so that search treats it as one word. */
function uniqueName(base) {
  counter += 1;
  return `${base}${run}x${counter}`;
}

function row(page, title) {
  return page.getByRole('listitem').filter({ hasText: title });
}

/** Opens the new-chat dialog and leaves it on the character form, whether or not characters exist yet. */
async function openCharacterForm(page) {
  await page.getByRole('button', { name: 'Nuova conversazione' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  // The dialog paints the picker once before its loading note, and shows the form at once when no character exists, so
  // no single look tells that it has settled. Click "Nuovo personaggio" whenever it shows until the name field does;
  // the click only resets an empty form, so repeating it is harmless.
  const create = dialog.getByRole('button', { name: 'Nuovo personaggio' });
  const nameField = dialog.getByLabel('Nome', { exact: true });
  await expect(async () => {
    if (!(await nameField.isVisible()) && (await create.isVisible())) {
      await create.click({ timeout: 1_000 });
    }
    await expect(nameField).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
  return dialog;
}

/** Creates a character with the minimal form and starts a chat with it; the chat's title is the character's name. */
async function startChatWithNewCharacter(page, firstName) {
  const dialog = await openCharacterForm(page);
  await dialog.getByLabel('Nome', { exact: true }).fill(firstName);
  await dialog.getByLabel('Età').fill('30');
  await dialog.getByRole('button', { name: 'Salva personaggio' }).click();
  await dialog.getByRole('button', { name: 'Inizia conversazione' }).click();
  await expect(dialog).toBeHidden();
  await expect(row(page, firstName)).toBeVisible();
}

async function chooseAction(page, title, action) {
  await row(page, title).getByRole('button', { name: `Azioni per ${title}` }).click();
  await row(page, title).getByRole('button', { name: action }).click();
}

test.beforeEach(async ({ page }) => {
  await page.goto('/chats');
  await expect(page.getByRole('heading', { level: 1, name: 'Conversazioni' })).toBeVisible();
});

test('the minimal character form validates, creates a character and starts a chat (T-MSG-01)', async ({ page }) => {
  const name = uniqueName('Alba');
  const dialog = await openCharacterForm(page);

  // Nothing is sent while a required value is wrong, and the reasons are shown in Italian.
  await dialog.getByLabel('Età').fill('17');
  await dialog.getByRole('button', { name: 'Salva personaggio' }).click();
  await expect(dialog.getByText('Inserisci un nome da 1 a 80 caratteri.')).toBeVisible();
  await expect(dialog.getByText('L\'età deve essere un numero intero da 18 a 120.')).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);

  await dialog.getByLabel('Nome', { exact: true }).fill(name);
  await dialog.getByLabel('Cognome (facoltativo)').fill('Rossi');
  await dialog.getByLabel('Età').fill('30');
  await dialog.getByLabel('Come parla di sé').selectOption({ label: 'Al femminile' });
  await dialog.getByRole('button', { name: 'Salva personaggio' }).click();

  // The new character is selected in the picker; the chat starts from there.
  await expect(dialog.getByLabel('Personaggio')).toContainText(`${name} Rossi`);
  await dialog.getByRole('button', { name: 'Inizia conversazione' }).click();
  await expect(dialog).toBeHidden();
  await expect(row(page, `${name} Rossi`)).toBeVisible();

  // The chat is stored: it is still there after a reload, and the character can start another one.
  await page.reload();
  await expect(row(page, `${name} Rossi`)).toBeVisible();
  await page.getByRole('button', { name: 'Nuova conversazione' }).click();
  await expect(page.getByRole('dialog').getByLabel('Personaggio')).toContainText(`${name} Rossi`);
  await page.getByRole('button', { name: 'Annulla' }).click();
  await expect(page.getByRole('dialog')).toBeHidden();

  const list = await new AxeBuilder({ page }).analyze();
  expect(list.violations.map((violation) => ({ rule: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
});

test('pin, archive, delete, restore and search work on the list (T-MSG-11)', async ({ page }) => {
  const first = uniqueName('Livia');
  const second = uniqueName('Marta');
  await startChatWithNewCharacter(page, first);
  await startChatWithNewCharacter(page, second);

  // The newest chat is first; pinning the older one moves it to the top, and unpinning puts it back.
  await expect(page.getByRole('listitem').first()).toContainText(second);
  await chooseAction(page, first, 'Fissa in alto');
  await expect(page.getByRole('listitem').first()).toContainText(first);
  await expect(row(page, first)).toContainText('Fissata');
  await chooseAction(page, first, 'Rimuovi dalla cima');
  await expect(row(page, first)).not.toContainText('Fissata');
  await expect(page.getByRole('listitem').first()).toContainText(second);

  // Archive moves a chat to its own tab, and the tab can bring it back.
  await chooseAction(page, first, 'Archivia');
  await expect(row(page, first)).toBeHidden();
  await page.getByRole('button', { name: 'Archiviate' }).click();
  await expect(row(page, first)).toBeVisible();
  await expect(row(page, second)).toBeHidden();
  await chooseAction(page, first, 'Ripristina dall\'archivio');
  await expect(row(page, first)).toBeHidden();
  await page.getByRole('button', { name: 'Attive' }).click();
  await expect(row(page, first)).toBeVisible();

  // Search keeps the matching chat only, says so when nothing matches, and the full list returns when cleared.
  const search = page.getByLabel('Cerca nelle conversazioni');
  await search.fill(first);
  await expect(row(page, first)).toBeVisible();
  await expect(row(page, second)).toBeHidden();
  await search.fill('parolainesistentexyz');
  await expect(page.getByText('Nessuna conversazione corrisponde alla ricerca.')).toBeVisible();
  await search.fill('');
  await expect(row(page, first)).toBeVisible();
  await expect(row(page, second)).toBeVisible();

  // Delete removes the chat and offers to restore it.
  await chooseAction(page, first, 'Elimina');
  await expect(row(page, first)).toBeHidden();
  await expect(page.getByText(`Conversazione «${first}» eliminata.`)).toBeVisible();
  await page.getByRole('button', { name: 'Ripristina', exact: true }).click();
  await expect(row(page, first)).toBeVisible();
  await expect(page.getByText(`Conversazione «${first}» eliminata.`)).toBeHidden();

  // A deleted chat stays deleted after a reload; the second chat was never touched.
  await chooseAction(page, second, 'Elimina');
  await page.reload();
  await expect(row(page, first)).toBeVisible();
  await expect(row(page, second)).toBeHidden();
});
