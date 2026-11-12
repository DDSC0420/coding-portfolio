import { defineConfig } from '@playwright/test';

// The UI tests run the real stack: PostgreSQL (RELAY_TEST_ADMIN_URL), then the demo CRM, the API
// and two workers as plain processes (scripts/run_local.py), with the built dashboard in front.
export default defineConfig({
  testDir: 'tests',
  timeout: 90_000,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    viewport: { width: 1360, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
  },
  webServer: [
    {
      command: 'cd .. && uv run python scripts/run_local.py --fresh',
      url: 'http://127.0.0.1:8000/health/ready',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'npm run build && npm run preview',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
