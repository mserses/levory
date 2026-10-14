import { useEffect, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Spinner } from '@/components/Spinner';
import { ErrorState } from '@/components/ErrorState';
import { ForbiddenPage } from '@/app/pages/ForbiddenPage';
import type { Role } from './roles';
import { useAuth } from './useAuth';

export interface RequireRoleProps {
  /** Bu rollerden biri gerekir (admin her zaman geçer). Boş dizi: oturum açmış herkes. */
  roles: readonly Role[];
  children: ReactNode;
}

/**
 * Rota koruması. Oturum yoksa girişe yönlendirir (OIDC: IdP'ye, dev: /giris),
 * rol yetmezse 403 sayfası gösterir. Asıl yetki kontrolü her zaman backend'dedir.
 */
export function RequireRole({ roles, children }: RequireRoleProps) {
  const auth = useAuth();
  const location = useLocation();
  const returnTo = location.pathname + location.search + location.hash;
  const { status, mode, login } = auth;

  useEffect(() => {
    if (status === 'anonymous' && mode === 'oidc') void login(returnTo);
  }, [status, mode, login, returnTo]);

  if (status === 'loading') return <Spinner block label="Oturum doğrulanıyor…" />;
  if (status === 'signing_out') return <Spinner block label="Çıkış yapılıyor…" showLabel />;
  if (status === 'error') {
    return (
      <ErrorState
        title="Oturum doğrulanamadı"
        message={auth.error ?? undefined}
        onRetry={() => {
          window.location.reload();
        }}
      />
    );
  }
  if (status === 'anonymous') {
    if (mode === 'oidc')
      return <Spinner block label="Giriş sayfasına yönlendiriliyorsunuz…" showLabel />;
    return <Navigate to="/giris" replace state={{ from: returnTo }} />;
  }
  if (!auth.hasRole(...roles) && roles.length > 0) return <ForbiddenPage />;
  return <>{children}</>;
}
