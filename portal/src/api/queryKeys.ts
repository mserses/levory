/**
 * TanStack Query anahtarları — tek kaynak. Mutasyondan sonra ilgili kökü geçersiz kılın:
 *   queryClient.invalidateQueries({ queryKey: queryKeys.incidents.all })
 */
import type { ArticleListQuery, DecisionInboxQuery, IncidentListQuery } from './types';

export const queryKeys = {
  me: ['me'] as const,
  incidents: {
    all: ['incidents'] as const,
    /** Tüm liste sorguları (filtreden bağımsız geçersiz kılmak için) */
    lists: ['incidents', 'list'] as const,
    list: (query: IncidentListQuery) => ['incidents', 'list', query] as const,
    detail: (id: string) => ['incidents', 'detail', id] as const,
    decisions: (id: string) => ['incidents', 'detail', id, 'decisions'] as const,
    slaAlerts: ['incidents', 'sla-alerts'] as const,
  },
  decisions: {
    all: ['decisions'] as const,
    inbox: (query: DecisionInboxQuery) => ['decisions', 'inbox', query] as const,
  },
  knowledge: {
    all: ['knowledge'] as const,
    list: (query: ArticleListQuery) => ['knowledge', 'list', query] as const,
    detail: (id: string) => ['knowledge', 'detail', id] as const,
    search: (q: string) => ['knowledge', 'search', q] as const,
  },
  audit: {
    all: ['audit'] as const,
    page: (limit: number, offset: number) => ['audit', 'page', limit, offset] as const,
    verify: ['audit', 'verify'] as const,
  },
  integrations: {
    all: ['integrations'] as const,
    status: ['integrations', 'status'] as const,
    conflicts: ['integrations', 'conflicts'] as const,
  },
} as const;
