import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { configureApiClient } from '@/api/client';
import { errorMessage, isApiError } from '@/api/errors';
import { meApi } from '@/api/me';
import { config } from '@/config/env';
import { createAdapter, type AuthAdapter } from './adapters';
import {
  AuthContext,
  DEFAULT_AI_CONFIDENCE_THRESHOLD,
  type AuthContextValue,
  type AuthStatus,
  type AuthUser,
} from './AuthContext';
import { buildDevToken } from './devToken';
import { hasAnyRole, isRequesterOnly, type Role } from './roles';

interface Props {
  children: ReactNode;
  /** Testler/özel kurulumlar için; verilmezse ortam ayarlarından oluşturulur */
  adapter?: AuthAdapter;
}

export function AuthProvider({ children, adapter: given }: Props) {
  const adapter = useMemo(() => given ?? createAdapter(config), [given]);
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(DEFAULT_AI_CONFIDENCE_THRESHOLD);
  const statusRef = useRef(status);
  statusRef.current = status;

  const resetSession = useCallback(async () => {
    await adapter.clear();
    queryClient.clear();
    setUser(null);
    setStatus('anonymous');
  }, [adapter, queryClient]);

  // 401 -> oturumu sıfırla (yalnızca oturum açıkken; döngüye girmez)
  const onUnauthorizedRef = useRef<() => void>(() => undefined);
  onUnauthorizedRef.current = () => {
    if (statusRef.current === 'authenticated') void resetSession();
  };

  // API istemcisini bu oturuma bağla. İlk bağlama render sırasında yapılır: çocukların
  // efektleri (ilk sorgular) ebeveyn efektinden önce çalışır. Efekt, StrictMode'un
  // sök-tak döngüsünden sonra yeniden bağlar ve gerçek söküşte bağı kaldırır.
  const bind = useCallback(() => {
    configureApiClient({
      getAccessToken: () => adapter.getAccessToken(),
      onUnauthorized: () => {
        onUnauthorizedRef.current();
      },
    });
  }, [adapter]);
  const boundAdapter = useRef<AuthAdapter | null>(null);
  if (boundAdapter.current !== adapter) {
    boundAdapter.current = adapter;
    bind();
  }
  useEffect(() => {
    bind();
    return () => {
      configureApiClient({ getAccessToken: () => null, onUnauthorized: () => undefined });
    };
  }, [bind]);

  const loadMe = useCallback(async () => {
    try {
      const me = await meApi.get();
      setUser({ username: me.username, roles: me.roles });
      setThreshold(me.ai_confidence_threshold);
      setError(null);
      setStatus('authenticated');
    } catch (e) {
      if (isApiError(e) && e.isUnauthorized) {
        await adapter.clear();
        setUser(null);
        setStatus('anonymous');
        return;
      }
      setError(errorMessage(e));
      setStatus('error');
    }
  }, [adapter]);

  // İlk açılış: mevcut oturum varsa /me ile doğrula
  useEffect(() => {
    const run = { cancelled: false };
    void (async () => {
      const token = await adapter.getAccessToken();
      if (run.cancelled) return;
      if (!token) {
        setStatus('anonymous');
        return;
      }
      await loadMe();
    })();
    return () => {
      run.cancelled = true;
    };
  }, [adapter, loadMe]);

  const login = useCallback(
    async (returnTo = '/') => {
      await adapter.login(returnTo);
    },
    [adapter],
  );

  const loginDev = useCallback(
    async (username: string, roles: Role[]) => {
      if (!adapter.setDevToken) throw new Error('Geliştirme girişi bu kipte kullanılamaz.');
      adapter.setDevToken(buildDevToken(username, roles));
      setStatus('loading');
      await loadMe();
    },
    [adapter, loadMe],
  );

  const logout = useCallback(async () => {
    queryClient.clear();
    setUser(null);
    if (adapter.mode === 'oidc') {
      // IdP'ye çıkış yönlendirmesi: başarılıysa sayfa ayrılır (söz çözülmez). Bu sırada
      // "anonymous" olursak RequireRole hemen yeniden giriş başlatır ve çıkışla yarışır.
      setStatus('signing_out');
      try {
        await adapter.logout();
      } finally {
        setStatus('anonymous');
      }
      return;
    }
    setStatus('anonymous');
    await adapter.logout();
  }, [adapter, queryClient]);

  const completeLogin = useCallback(async () => {
    if (!adapter.handleCallback) return '/';
    const target = await adapter.handleCallback();
    setStatus('loading');
    await loadMe();
    return target;
  }, [adapter, loadMe]);

  const value = useMemo<AuthContextValue>(() => {
    const roles = user?.roles ?? [];
    return {
      status,
      mode: adapter.mode,
      user,
      roles,
      aiConfidenceThreshold: threshold,
      hasRole: (...required: Role[]) => user !== null && hasAnyRole(roles, required),
      isRequesterOnly: user !== null && isRequesterOnly(roles),
      login,
      loginDev,
      logout,
      completeLogin,
      error,
    };
  }, [status, adapter.mode, user, threshold, login, loginDev, logout, completeLogin, error]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
