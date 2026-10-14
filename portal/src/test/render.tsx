/**
 * Test render yardımcıları. Gerçek AuthProvider + sahte (dev) bağdaştırıcı + MSW `/me`
 * kullanılır; yani rol davranışı uygulamadakiyle aynıdır.
 *
 *   renderWithProviders(<IncidentQueuePage />, { user: { username: 'ayse', roles: ['agent'] } })
 *   renderApp({ route: '/kayitlar', user: AGENT })   // tam rota tablosu + AppShell
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement, ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ROUTER_FUTURE } from '@/app/routerFuture';
import { AppRoutes } from '@/app/routes';
import type { AuthAdapter } from '@/auth/adapters';
import { AuthProvider } from '@/auth/AuthProvider';
import { buildDevToken } from '@/auth/devToken';
import type { Role } from '@/auth/roles';
import { ToastProvider } from '@/components/Toast';

export interface TestUser {
  username: string;
  roles: Role[];
}

export const REQUESTER: TestUser = { username: 'zeynep', roles: ['requester'] };
export const AGENT: TestUser = { username: 'ayse', roles: ['agent'] };
export const APPROVER: TestUser = { username: 'sef.ali', roles: ['approver'] };
export const ADMIN: TestUser = { username: 'yonetici', roles: ['admin'] };

export function createTestAdapter(user: TestUser | null): AuthAdapter & { token: string | null } {
  const adapter = {
    mode: 'dev' as const,
    token: user ? buildDevToken(user.username, user.roles) : null,
    getAccessToken: () => Promise.resolve(adapter.token),
    login: () => Promise.resolve(),
    setDevToken: (t: string) => {
      adapter.token = t;
    },
    clear: () => {
      adapter.token = null;
      return Promise.resolve();
    },
    logout: () => adapter.clear(),
  };
  return adapter;
}

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
}

interface Options {
  /** null = oturum yok */
  user?: TestUser | null;
  /** Başlangıç adresi */
  route?: string;
  /** Bileşenin bağlanacağı rota deseni (useParams için), örn. '/kayitlar/:id' */
  path?: string;
  queryClient?: QueryClient;
}

function Providers({
  children,
  user,
  route,
  queryClient,
}: Required<Omit<Options, 'path'>> & { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]} future={ROUTER_FUTURE}>
        <ToastProvider>
          <AuthProvider adapter={createTestAdapter(user)}>{children}</AuthProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

export function renderWithProviders(
  ui: ReactElement,
  { user = AGENT, route = '/', path, queryClient = createTestQueryClient() }: Options = {},
): RenderResult & { user: ReturnType<typeof userEvent.setup>; queryClient: QueryClient } {
  const content = path ? (
    <Routes>
      <Route path={path} element={ui} />
    </Routes>
  ) : (
    ui
  );
  const result = render(
    <Providers user={user} route={route} queryClient={queryClient}>
      {content}
    </Providers>,
  );
  return { ...result, user: userEvent.setup(), queryClient };
}

/** Tam uygulama (rota tablosu + kabuk + korumalar) */
export function renderApp(options: Options = {}) {
  return renderWithProviders(<AppRoutes />, options);
}
