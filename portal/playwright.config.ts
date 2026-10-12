import { defineConfig, devices } from '@playwright/test';

/**
 * Uçtan uca testler: gerçek backend (geçici SQLite, dev kimlik doğrulaması, sahte LLM) +
 * Vite geliştirme sunucusu. `npm run e2e`. Senaryolar aynı veritabanını paylaşır; seri çalışır.
 */
const API_PORT = process.env.E2E_API_PORT ?? '8765';
const WEB_PORT = process.env.E2E_WEB_PORT ?? '5180';
const API_URL = `http://127.0.0.1:${API_PORT}`;
// E2E_WEB_URL verilirse Vite başlatılmaz; senaryolar o adresteki portala (ör. nginx imajı) koşar
const EXTERNAL_WEB_URL = process.env.E2E_WEB_URL;
const WEB_URL = EXTERNAL_WEB_URL ?? `http://127.0.0.1:${WEB_PORT}`;
const CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: WEB_URL,
    locale: 'tr-TR',
    timezoneId: 'Europe/Istanbul',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'node e2e/start-backend.mjs',
      url: `${API_URL}/health`,
      env: { E2E_API_PORT: API_PORT, E2E_PORTAL_ORIGINS: WEB_URL },
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'ignore',
    },
    ...(EXTERNAL_WEB_URL
      ? []
      : [
          {
            command: `npx vite --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
            url: WEB_URL,
            env: { VITE_API_BASE_URL: API_URL, VITE_AUTH_MODE: 'dev' },
            reuseExistingServer: false,
            timeout: 120_000,
          },
        ]),
  ],
});
