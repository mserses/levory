/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  // E2E iki geliştirme sunucusunu (Faz 1/2 + Faz 3) aynı kökte çalıştırır: önbellekler ayrı olsun
  ...(process.env.VITE_CACHE_DIR ? { cacheDir: process.env.VITE_CACHE_DIR } : {}),
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { port: 5173, strictPort: true },
  preview: { port: 4173 },
  build: { sourcemap: true, target: 'es2022' },
  css: { modules: { localsConvention: 'camelCaseOnly' } },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Playwright senaryoları (e2e/) Vitest'e dahil değildir
    include: ['src/**/*.test.{ts,tsx}'],
    css: { modules: { classNameStrategy: 'non-scoped' } },
    restoreMocks: true,
    // Testler deterministik: sabit saat dilimi ve API adresi
    env: {
      TZ: 'Europe/Istanbul',
      VITE_API_BASE_URL: 'http://api.test',
      VITE_AUTH_MODE: 'dev',
    },
  },
});
