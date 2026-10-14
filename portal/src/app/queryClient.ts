import { QueryClient } from '@tanstack/react-query';
import { isApiError } from '@/api/errors';

/** Ortak sorgu ayarları: 4xx'te yeniden deneme yok (401/403/404/409 anlamlı sonuçlardır). */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        retry: (failureCount, error) => {
          if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
          return failureCount < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}
