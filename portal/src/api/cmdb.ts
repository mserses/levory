import { api } from './client';
import type { CIListQuery, CIRelationship, ConfigurationItem, ImpactAnalysis } from './types';

const path = (id: string) => `/cmdb/cis/${encodeURIComponent(id)}`;

/** CMDB uçları (okuma: agent/cab/change_manager). */
export const cmdbApi = {
  list: (query: CIListQuery = {}, signal?: AbortSignal) =>
    api.get<ConfigurationItem[]>('/cmdb/cis', { query, signal }),

  get: (id: string, signal?: AbortSignal) => api.get<ConfigurationItem>(path(id), { signal }),

  /** İki yönlü ilişkiler (kaynak ya da hedef bu CI) */
  relationships: (id: string, signal?: AbortSignal) =>
    api.get<CIRelationship[]>(`${path(id)}/relationships`, { signal }),

  /** "Bu CI değişirse kim etkilenir" (yukarı yönlü bağımlılar) */
  impact: (id: string, maxDepth = 5, signal?: AbortSignal) =>
    api.get<ImpactAnalysis>(`${path(id)}/impact`, { query: { max_depth: maxDepth }, signal }),

  /** Birden çok CI için etki analizi (henüz kaydedilmemiş değişiklik önizlemesi) */
  impactFor: (ciIds: readonly string[], maxDepth = 5, signal?: AbortSignal) =>
    api.post<ImpactAnalysis>('/cmdb/impact', { ci_ids: ciIds, max_depth: maxDepth }, { signal }),
};
