import { defineConfig, devices } from '@playwright/test';

/**
 * Uçtan uca testler: gerçek backend (geçici SQLite, dev kimlik doğrulaması, sahte LLM) +
 * Vite geliştirme sunucusu. `npm run e2e`. Senaryolar aynı veritabanını paylaşır; seri çalışır.
 */
const API_PORT = process.env.E2E_API_PORT ?? '8765';
const WEB_PORT = process.env.E2E_WEB_PORT ?? '5180';
const API_URL = `http://127.0.0.1:${API_PORT}`;
// Faz 3 (otomasyon) senaryoları ayrı backend + portal çiftine koşar: gölge mod kapalı, sahte dizin
const FAZ3_API_PORT = process.env.E2E_FAZ3_API_PORT ?? '8766';
const FAZ3_WEB_PORT = process.env.E2E_FAZ3_WEB_PORT ?? '5181';
const FAZ3_API_URL = `http://127.0.0.1:${FAZ3_API_PORT}`;
const FAZ3_WEB_URL = process.env.E2E_FAZ3_WEB_URL ?? `http://127.0.0.1:${FAZ3_WEB_PORT}`;
const FAZ3_SPECS = /faz3-.*\.spec\.ts$/;
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
  projects: [
    { name: 'chromium', testIgnore: FAZ3_SPECS, use: { ...devices['Desktop Chrome'] } },
    {
      name: 'faz3',
      testMatch: FAZ3_SPECS,
      use: { ...devices['Desktop Chrome'], baseURL: FAZ3_WEB_URL },
    },
  ],
  webServer: [
    {
      command: 'node e2e/start-backend.mjs',
      url: `${API_URL}/health`,
      env: { E2E_API_PORT: API_PORT, E2E_PORTAL_ORIGINS: WEB_URL },
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'ignore',
    },
    {
      command: 'node e2e/start-backend.mjs',
      url: `${FAZ3_API_URL}/health`,
      env: { E2E_API_PORT: FAZ3_API_PORT, E2E_PORTAL_ORIGINS: FAZ3_WEB_URL, E2E_PROFILE: 'faz3' },
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'ignore',
    },
    ...(process.env.E2E_FAZ3_WEB_URL
      ? []
      : [
          {
            command: `npx vite --host 127.0.0.1 --port ${FAZ3_WEB_PORT} --strictPort`,
            url: FAZ3_WEB_URL,
            env: {
              VITE_API_BASE_URL: FAZ3_API_URL,
              VITE_AUTH_MODE: 'dev',
              VITE_CACHE_DIR: 'node_modules/.vite-e2e-faz3',
            },
            reuseExistingServer: false,
            timeout: 120_000,
          },
        ]),
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
