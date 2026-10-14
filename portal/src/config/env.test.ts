import { describe, expect, it } from 'vitest';
import { parseConfig } from './env';

describe('parseConfig', () => {
  it('dev kipi ve varsayılanlar', () => {
    const c = parseConfig({ VITE_AUTH_MODE: 'dev', VITE_API_BASE_URL: 'http://x.test/' });
    expect(c).toMatchObject({
      apiBaseUrl: 'http://x.test',
      authMode: 'dev',
      oidc: null,
      error: null,
      timeZone: 'Europe/Istanbul',
    });
  });

  it('oidc kipi authority ve client id ister', () => {
    const c = parseConfig({ VITE_AUTH_MODE: 'oidc', VITE_OIDC_AUTHORITY: '' });
    expect(c.error).toMatch(/VITE_OIDC_AUTHORITY/);
    expect(c.error).toMatch(/VITE_OIDC_CLIENT_ID/);
  });

  it('oidc ayarları', () => {
    const c = parseConfig({
      VITE_AUTH_MODE: 'oidc',
      VITE_OIDC_AUTHORITY: 'https://sso.test/realms/sz',
      VITE_OIDC_CLIENT_ID: 'portal',
    });
    expect(c.oidc).toEqual({
      authority: 'https://sso.test/realms/sz',
      clientId: 'portal',
      scope: 'openid profile email',
    });
    expect(c.error).toBeNull();
  });
});
