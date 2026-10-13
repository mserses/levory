/**
 * TanStack Query anahtarları — tek kaynak. Mutasyondan sonra ilgili kökü geçersiz kılın:
 *   queryClient.invalidateQueries({ queryKey: queryKeys.incidents.all })
 */
import type {
  ArticleListQuery,
  ChangeListQuery,
  CIListQuery,
  DecisionInboxQuery,
  IncidentListQuery,
} from './types';

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
  changes: {
    all: ['changes'] as const,
    lists: ['changes', 'list'] as const,
    list: (query: ChangeListQuery) => ['changes', 'list', query] as const,
    detail: (id: string) => ['changes', 'detail', id] as const,
    approvals: (id: string) => ['changes', 'detail', id, 'approvals'] as const,
    decisions: (id: string) => ['changes', 'detail', id, 'decisions'] as const,
    impact: (id: string) => ['changes', 'detail', id, 'impact'] as const,
    conflicts: (id: string) => ['changes', 'detail', id, 'conflicts'] as const,
    calendar: (from: string, to: string) => ['changes', 'calendar', from, to] as const,
    templates: ['changes', 'templates'] as const,
  },
  cmdb: {
    all: ['cmdb'] as const,
    list: (query: CIListQuery) => ['cmdb', 'list', query] as const,
    detail: (id: string) => ['cmdb', 'detail', id] as const,
    relationships: (id: string) => ['cmdb', 'detail', id, 'relationships'] as const,
    impact: (id: string, maxDepth: number) => ['cmdb', 'detail', id, 'impact', maxDepth] as const,
    /** Kaydedilmemiş CI seçimi için etki önizlemesi (sıralı id listesi) */
    impactFor: (ciIds: readonly string[], maxDepth: number) =>
      ['cmdb', 'impact', [...ciIds].sort(), maxDepth] as const,
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
