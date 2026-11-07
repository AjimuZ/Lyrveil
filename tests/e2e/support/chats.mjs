// Helpers for the specs that start from a new chat: unique names and the minimal character form.
import { expect } from '@playwright/test';

const run = Date.now().toString(36);
let counter = 0;

/** Returns a first name no other test or run uses, made of letters and digits so that search treats it as one word. */
export function uniqueName(base) {
  counter += 1;
  return `${base}${run}y${counter}`;
}

/** Creates a character with the minimal form and starts a chat with it; the chat's title is the character's name. */
export async function startChatWithNewCharacter(page, firstName) {
  await page.getByRole('button', { name: 'Nuova conversazione' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  // "Annulla" is on both steps and absent while the characters load, so it tells that the dialog has settled.
  await expect(dialog.getByRole('button', { name: 'Annulla' })).toBeVisible();
  // The picker offers "Nuovo personaggio"; with no character yet the dialog opens on the form itself.
  const create = dialog.getByRole('button', { name: 'Nuovo personaggio' });
  const nameField = dialog.getByLabel('Nome', { exact: true });
  // The dialog can repaint under the click (the picker is drawn before its loading state), which drops the click, so
  // the click is repeated until the form is there.
  await expect(async () => {
    if (!(await nameField.isVisible())) {
      await create.click({ timeout: 1000 });
    }
    await expect(nameField).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  await nameField.fill(firstName);
  await dialog.getByLabel('Età').fill('30');
  await dialog.getByRole('button', { name: 'Salva personaggio' }).click();
  await dialog.getByRole('button', { name: 'Inizia conversazione' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('listitem').filter({ hasText: firstName })).toBeVisible();
}

/**
 * Opens the chat of the row titled `title` and returns its id, read from the address. When a character has several
 * chats the list shows the most recent first, and that one is opened.
 */
export async function openChat(page, title) {
  await page.getByRole('listitem').filter({ hasText: title }).first().getByRole('link').click();
  await expect(page).toHaveURL(/\/chats\/[^/]+$/);
  await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
  return new URL(page.url()).pathname.split('/').pop();
}

/** Types `text` in the composer and sends it with Enter. */
export async function sendText(page, text) {
  const composer = page.getByLabel('Scrivi un messaggio');
  await composer.fill(text);
  await composer.press('Enter');
}

/** The bubble of the message that contains `text`. */
export function bubble(page, text) {
  return page.getByRole('listitem').filter({ hasText: text });
}
