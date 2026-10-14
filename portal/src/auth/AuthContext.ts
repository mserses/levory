import { createContext } from 'react';
import type { Role } from './roles';

/**
 * signing_out: OIDC çıkış yönlendirmesi sürüyor. Bu sırada korunan rotalar yeniden girişi
 * (signinRedirect) BAŞLATMAZ; aksi halde çıkış ile giriş yönlendirmesi yarışır.
 */
export type AuthStatus = 'loading' | 'anonymous' | 'authenticated' | 'error' | 'signing_out';

export interface AuthUser {
  username: string;
  /** Sunucunun /me yanıtındaki roller (bilinmeyen roller de korunur) */
  roles: string[];
}

/** Backend SZ_AI_CONFIDENCE_THRESHOLD varsayılanı; /me yanıtı gelene kadar kullanılır */
export const DEFAULT_AI_CONFIDENCE_THRESHOLD = 0.75;

export interface AuthContextValue {
  status: AuthStatus;
  mode: 'oidc' | 'dev';
  user: AuthUser | null;
  roles: string[];
  /** Politika güven eşiği (backend SZ_AI_CONFIDENCE_THRESHOLD, /me ile gelir) */
  aiConfidenceThreshold: number;
  /** Kullanıcı verilen rollerden birine sahip mi (admin her şeyi kapsar) */
  hasRole: (...roles: Role[]) => boolean;
  /** Yalnızca self-servis (requester) kullanıcı mı */
  isRequesterOnly: boolean;
  /** OIDC: IdP'ye yönlendirir. dev: giriş sayfasını kullanın (loginDev). */
  login: (returnTo?: string) => Promise<void>;
  /** Dev kipi girişi: belirteci üretir, /me ile doğrular */
  loginDev: (username: string, roles: Role[]) => Promise<void>;
  logout: () => Promise<void>;
  /** OIDC geri dönüşünü tamamlar; dönülecek yolu verir */
  completeLogin: () => Promise<string>;
  error: string | null;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
