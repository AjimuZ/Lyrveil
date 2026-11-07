// A client route two segments deep loads on a direct visit with no console error (regression: a <base> element that the
// Content-Security-Policy refuses made the assets resolve under /chats/ and the page stay empty).
import { expect, test } from '@playwright/test';

test('a deep client route loads directly, with no console error and no failed request', async ({ page }) => {
  const problems = [];
  page.on('console', (message) => message.type() === 'error' && problems.push(message.text()));
  page.on('pageerror', (error) => problems.push(error.message));
  page.on('requestfailed', (request) => problems.push(`${request.url()} ${request.failure()?.errorText}`));

  await page.goto('/chats/00000000-0000-7000-8000-000000000000');
  await expect(page.getByRole('link', { name: 'Torna alle conversazioni' })).toBeVisible();
  await expect(page.getByText('Elemento non trovato.')).toBeVisible();
  // The 404 of the missing chat is the one expected failure to load a resource.
  expect(problems.filter((problem) => !problem.includes('404'))).toEqual([]);
});
