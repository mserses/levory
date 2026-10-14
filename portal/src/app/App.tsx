import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from '@/auth/AuthProvider';
import { ErrorState } from '@/components/ErrorState';
import { ToastProvider } from '@/components/Toast';
import { config } from '@/config/env';
import { createQueryClient } from './queryClient';
import { ROUTER_FUTURE } from './routerFuture';
import { AppRoutes } from './routes';

export function App({ queryClient }: { queryClient?: QueryClient }) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  if (config.error) {
    return (
      <main style={{ padding: 'var(--space-6)' }}>
        <ErrorState title="Yapılandırma hatası" message={config.error} />
      </main>
    );
  }
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter future={ROUTER_FUTURE}>
        <ToastProvider>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
