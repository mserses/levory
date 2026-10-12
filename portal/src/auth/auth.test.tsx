import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ROUTER_FUTURE } from '@/app/routerFuture';
import { api } from '@/api/client';
import { apiUrl } from '@/test/handlers';
import { ADMIN, AGENT, REQUESTER, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { AuthAdapter } from './adapters';
import { AuthProvider } from './AuthProvider';
import { buildDevToken } from './devToken';
import { RequireRole } from './RequireRole';
import { hasAnyRole, isRequesterOnly } from './roles';
import { useAuth } from './useAuth';

function WhoAmI() {
  const auth = useAuth();
  return (
    <div>
      <p>durum: {auth.status}</p>
      <p>kullanıcı: {auth.user?.username ?? '-'}</p>
      <p>agent mı: {String(auth.hasRole('agent'))}</p>
      <p>onaylayıcı mı: {String(auth.hasRole('approver'))}</p>
      <p>self-servis: {String(auth.isRequesterOnly)}</p>
    </div>
  );
}

describe('roller', () => {
  it('admin her rolü kapsar; boş liste herkese açık', () => {
    expect(hasAnyRole(['admin'], ['approver'])).toBe(true);
    expect(hasAnyRole(['agent'], ['approver'])).toBe(false);
    expect(hasAnyRole([], [])).toBe(true);
    expect(isRequesterOnly(['requester'])).toBe(true);
    expect(isRequesterOnly(['requester', 'agent'])).toBe(false);
    expect(isRequesterOnly(['admin'])).toBe(false);
  });

  it('dev belirteci', () => {
    expect(buildDevToken(' ayse ', ['agent', 'approver'])).toBe('dev:ayse:agent,approver');
    expect(() => buildDevToken('a:b', ['agent'])).toThrow(/Kullanıcı adı/);
  });
});

describe('AuthProvider + useAuth', () => {
  it('/me ile kullanıcıyı ve rolleri yükler', async () => {
    renderWithProviders(<WhoAmI />, { user: ADMIN });
    expect(await screen.findByText('durum: authenticated')).toBeInTheDocument();
    expect(screen.getByText('kullanıcı: yonetici')).toBeInTheDocument();
    expect(screen.getByText('agent mı: true')).toBeInTheDocument();
    expect(screen.getByText('self-servis: false')).toBeInTheDocument();
  });

  it('requester yalnızca self-servis', async () => {
    renderWithProviders(<WhoAmI />, { user: REQUESTER });
    expect(await screen.findByText('self-servis: true')).toBeInTheDocument();
    expect(screen.getByText('onaylayıcı mı: false')).toBeInTheDocument();
  });

  it('oturum yoksa anonymous', async () => {
    renderWithProviders(<WhoAmI />, { user: null });
    expect(await screen.findByText('durum: anonymous')).toBeInTheDocument();
  });

  it('/me 401 dönerse anonymous', async () => {
    server.use(http.get(apiUrl('/me'), () => HttpResponse.json({ detail: 'x' }, { status: 401 })));
    renderWithProviders(<WhoAmI />, { user: AGENT });
    expect(await screen.findByText('durum: anonymous')).toBeInTheDocument();
  });

  it('/me sunucu hatasında error durumu', async () => {
    server.use(
      http.get(apiUrl('/me'), () => HttpResponse.json({ detail: 'Bakımda' }, { status: 503 })),
    );
    renderWithProviders(<WhoAmI />, { user: AGENT });
    expect(await screen.findByText('durum: error')).toBeInTheDocument();
  });

  it('herhangi bir API 401 dönerse oturum sıfırlanır (yeniden giriş)', async () => {
    renderWithProviders(<WhoAmI />, { user: AGENT });
    await screen.findByText('durum: authenticated');
    server.use(
      http.get(apiUrl('/incidents'), () =>
        HttpResponse.json({ detail: 'süresi doldu' }, { status: 401 }),
      ),
    );
    await act(async () => {
      await api.get('/incidents').catch(() => undefined);
    });
    expect(await screen.findByText('durum: anonymous')).toBeInTheDocument();
  });
});

describe('RequireRole', () => {
  const page = (
    <RequireRole roles={['approver']}>
      <p>gizli onay ekranı</p>
    </RequireRole>
  );

  it('yetkili rol içeriği görür', async () => {
    renderWithProviders(page, { user: { username: 'ali', roles: ['approver'] } });
    expect(await screen.findByText('gizli onay ekranı')).toBeInTheDocument();
  });

  it('yetersiz rolde 403 sayfası', async () => {
    renderWithProviders(page, { user: AGENT });
    expect(
      await screen.findByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('gizli onay ekranı')).not.toBeInTheDocument();
  });

  it('yüklenirken gösterge', () => {
    renderWithProviders(page, { user: AGENT });
    expect(screen.getByRole('status')).toHaveTextContent('Oturum doğrulanıyor');
  });
});

describe('OIDC çıkışı', () => {
  it('çıkış yönlendirmesi sürerken yeniden giriş (signinRedirect) başlatılmaz', async () => {
    const login = vi.fn(() => Promise.resolve());
    // signoutRedirect sayfadan ayrılır: söz hiç çözülmez
    const logout = vi.fn(() => new Promise<void>(() => undefined));
    const adapter: AuthAdapter = {
      mode: 'oidc',
      getAccessToken: () => Promise.resolve(buildDevToken('ayse', ['agent'])),
      login,
      clear: () => Promise.resolve(),
      logout,
    };
    function Shell() {
      const auth = useAuth();
      return (
        <RequireRole roles={[]}>
          <p>içerik</p>
          <button type="button" onClick={() => void auth.logout()}>
            Çıkış
          </button>
        </RequireRole>
      );
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/kayitlar']} future={ROUTER_FUTURE}>
          <AuthProvider adapter={adapter}>
            <Shell />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole('button', { name: 'Çıkış' }));
    expect(logout).toHaveBeenCalledOnce();
    expect(login).not.toHaveBeenCalled();
    expect(await screen.findByText('Çıkış yapılıyor…')).toBeInTheDocument();
    expect(screen.queryByText('içerik')).not.toBeInTheDocument();
  });
});
