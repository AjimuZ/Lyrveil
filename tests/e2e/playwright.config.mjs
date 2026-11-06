// Playwright settings of the end-to-end level: one browser, against the published port of compose.test.yaml.
import { defineConfig, devices } from '@playwright/test';

// The same variable and default as compose.test.yaml, so the suite and the project agree on the port. Never 4200.
const port = process.env.LYRVEIL_TEST_HTTP_PORT || '14200';

export default defineConfig({
  testDir: './specs',
  // Tests share one project with one database, so they run one after another until a test needs isolation.
  workers: 1,
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    locale: 'it-IT',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
