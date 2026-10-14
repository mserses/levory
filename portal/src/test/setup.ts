import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { configureApiClient } from '@/api/client';
import { server } from './server';

// Tanımsız istek = test hatası (gerçek ağa asla çıkılmaz)
beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
  configureApiClient({ getAccessToken: () => null, onUnauthorized: () => undefined });
  window.sessionStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});
afterAll(() => {
  server.close();
});
