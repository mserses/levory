/**
 * Kimlik doğrulama bağdaştırıcıları. AuthProvider yalnızca bu arayüzü bilir;
 * testler sahte bir bağdaştırıcı verebilir.
 */
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts';
import type { AppConfig } from '@/config/env';
import { DEV_TOKEN_KEY } from './devToken';
import { safeAppPath } from './returnTo';

/** OIDC sessiz yenileme iframe'inin geri dönüş yolu (nginx bu yolda yalnızca aynı kökenden çerçevelemeye izin verir) */
export const SILENT_CALLBACK_PATH = '/auth/silent';

export interface AuthAdapter {
  readonly mode: 'oidc' | 'dev';
  /** Mevcut oturumun erişim belirteci (yoksa null) */
  getAccessToken(): Promise<string | null>;
  /** Kimlik sağlayıcıya yönlendirir (oidc) — dev kipinde giriş formu kullanılır */
  login(returnTo: string): Promise<void>;
  /** Dev kipi: belirteci saklar */
  setDevToken?(token: string): void;
  /** Oturumu yerelde temizler (401 sonrası) */
  clear(): Promise<void>;
  /** Oturumu kapatır (oidc: IdP oturumu da) */
  logout(): Promise<void>;
  /** OIDC geri dönüşünü işler; dönülecek uygulama yolunu verir */
  handleCallback?(): Promise<string>;
}

function safeSession(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Dev belirteci sessionStorage'da (sekme kapanınca silinir); asla localStorage değil. */
export function createDevAdapter(): AuthAdapter {
  let memory: string | null = safeSession()?.getItem(DEV_TOKEN_KEY) ?? null;
  return {
    mode: 'dev',
    getAccessToken: () => Promise.resolve(memory),
    login: () => Promise.resolve(),
    setDevToken(token) {
      memory = token;
      safeSession()?.setItem(DEV_TOKEN_KEY, token);
    },
    clear() {
      memory = null;
      safeSession()?.removeItem(DEV_TOKEN_KEY);
      return Promise.resolve();
    },
    logout() {
      return this.clear();
    },
  };
}

export function createOidcUserManager(oidc: NonNullable<AppConfig['oidc']>): UserManager {
  const origin = window.location.origin;
  return new UserManager({
    authority: oidc.authority,
    client_id: oidc.clientId,
    scope: oidc.scope,
    redirect_uri: `${origin}/auth/callback`,
    // Yenileme belirteci yoksa sessiz yenileme iframe ile yapılır; ayrı, hafif bir sayfa
    // (main.tsx uygulamayı kurmadan yalnızca yanıtı üst pencereye iletir)
    silent_redirect_uri: `${origin}${SILENT_CALLBACK_PATH}`,
    post_logout_redirect_uri: `${origin}/giris`,
    response_type: 'code', // Authorization Code + PKCE (oidc-client-ts varsayılan olarak PKCE kullanır)
    // Belirteçler sessionStorage'da (sekmeye özgü); localStorage kullanılmaz
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: true,
    monitorSession: false,
  });
}

export function createOidcAdapter(manager: UserManager): AuthAdapter {
  const usable = (user: User | null) => (user && !user.expired ? user.access_token : null);
  return {
    mode: 'oidc',
    async getAccessToken() {
      return usable(await manager.getUser());
    },
    async login(returnTo) {
      await manager.signinRedirect({ state: { returnTo } });
    },
    async clear() {
      await manager.removeUser();
    },
    async logout() {
      try {
        await manager.signoutRedirect();
      } catch {
        await manager.removeUser();
      }
    },
    async handleCallback() {
      const user = await manager.signinRedirectCallback();
      const state = user.state as { returnTo?: unknown } | undefined;
      // Açık yönlendirme yok: yalnızca uygulama içi yol
      return safeAppPath(state?.returnTo) ?? '/';
    },
  };
}

export function createAdapter(config: AppConfig): AuthAdapter {
  if (config.authMode === 'oidc' && config.oidc) {
    return createOidcAdapter(createOidcUserManager(config.oidc));
  }
  return createDevAdapter();
}
