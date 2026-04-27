import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.WHO_BASE_URL || 'http://localhost:8000';
const apiKey = process.env.WHO_API_KEY || '';

export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    extraHTTPHeaders: apiKey
      ? { Authorization: `Bearer ${apiKey}` }
      : {},
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],
});
