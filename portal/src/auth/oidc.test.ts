import { User } from 'oidc-client-ts';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AppConfig } from '@/config/env';
import { SILENT_CALLBACK_PATH, createOidcAdapter, createOidcUserManager } from './adapters';
import { handleSilentCallback } from './silent';

const OIDC = { authority: 'https://sso.ornek.com/realms/sz', clientId: 'portal', scope: 'openid' };
const CONFIG: AppConfig = {
  apiBaseUrl: 'http://api.test',
  authMode: 'oidc',
  oidc: OIDC,
  timeZone: 'Europe/Istanbul',
  error: null,
};

const dump = (storage: Storage) =>
  Array.from({ length: storage.length }, (_, i) => storage.getItem(storage.key(i) ?? '')).join('|');

afterEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
});

describe('OIDC UserManager', () => {
  it('sessiz yenileme ayrı bir geri dönüş adresi kullanır (tam uygulama iframe içinde açılmaz)', () => {
    const manager = createOidcUserManager(OIDC);
    expect(manager.settings.redirect_uri).toBe(`${window.location.origin}/auth/callback`);
    expect(manager.settings.silent_redirect_uri).toBe(
      `${window.location.origin}${SILENT_CALLBACK_PATH}`,
    );
    expect(SILENT_CALLBACK_PATH).not.toBe('/auth/callback');
  });

  it('belirteç sessionStorage’da tutulur, localStorage’a yazılmaz', async () => {
    const manager = createOidcUserManager(OIDC);
    await manager.storeUser(
      new User({
        access_token: 'gizli-erisim-belirteci',
        token_type: 'Bearer',
        profile: { sub: 'u1', iss: OIDC.authority, aud: 'portal', exp: 0, iat: 0 },
        expires_at: Math.floor(Date.now() / 1000) + 300,
      }),
    );
    expect(dump(window.localStorage)).not.toContain('gizli-erisim-belirteci');
    expect(dump(window.sessionStorage)).toContain('gizli-erisim-belirteci');
    await expect(createOidcAdapter(manager).getAccessToken()).resolves.toBe(
      'gizli-erisim-belirteci',
    );
  });
});

describe('OIDC geri dönüşü', () => {
  const adapterReturning = (returnTo: unknown) =>
    createOidcAdapter({
      signinRedirectCallback: vi.fn().mockResolvedValue({ state: { returnTo } }),
    } as never);

  it('yalnızca uygulama içi yola döner (açık yönlendirme yok)', async () => {
    await expect(adapterReturning('/kayitlar/1?x=1#y').handleCallback?.()).resolves.toBe(
      '/kayitlar/1?x=1#y',
    );
    for (const bad of ['//evil.example', '/\\evil.example', 'https://evil.example', 5]) {
      await expect(adapterReturning(bad).handleCallback?.()).resolves.toBe('/');
    }
  });
});

describe('sessiz yenileme sayfası', () => {
  it('/auth/silent: yalnızca signinSilentCallback çalışır, uygulama açılmaz', async () => {
    const signinSilentCallback = vi.fn().mockResolvedValue(undefined);
    const make = vi.fn(() => ({ signinSilentCallback }) as never);
    await expect(handleSilentCallback(SILENT_CALLBACK_PATH, CONFIG, make)).resolves.toBe(true);
    expect(make).toHaveBeenCalledWith(OIDC);
    expect(signinSilentCallback).toHaveBeenCalledOnce();
  });

  it('diğer yollar ve dev kipi etkilenmez', async () => {
    const make = vi.fn();
    await expect(handleSilentCallback('/kayitlar', CONFIG, make)).resolves.toBe(false);
    await expect(
      handleSilentCallback(SILENT_CALLBACK_PATH, { ...CONFIG, authMode: 'dev', oidc: null }, make),
    ).resolves.toBe(false);
    expect(make).not.toHaveBeenCalled();
  });

  it('hata olsa da uygulamayı iframe içinde açmaz', async () => {
    const make = vi.fn(
      () => ({ signinSilentCallback: vi.fn().mockRejectedValue(new Error('x')) }) as never,
    );
    await expect(handleSilentCallback(SILENT_CALLBACK_PATH, CONFIG, make)).resolves.toBe(true);
  });
});
