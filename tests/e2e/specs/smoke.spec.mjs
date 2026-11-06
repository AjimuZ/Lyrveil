// Smoke test: the empty app opens through nginx, shows its shell, and has no accessibility violation.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('the empty app opens on the chat list and has no axe violation', async ({ page }) => {
  await page.goto('/');

  // "/" redirects to the chat list, a placeholder screen until its phase delivers it.
  await expect(page).toHaveURL(/\/chats$/);
  await expect(page).toHaveTitle('Lyrveil');
  await expect(page.getByRole('navigation')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Conversazioni' })).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  // The rule ids and targets make a failure readable without opening the report.
  const summary = results.violations.map((violation) => ({
    rule: violation.id,
    impact: violation.impact,
    targets: violation.nodes.map((node) => node.target),
  }));
  expect(summary).toEqual([]);
  // An analysis that checked no rule would pass for the wrong reason.
  expect(results.passes.length).toBeGreaterThan(0);
});
