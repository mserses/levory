import { api } from './client';
import type { ActionListQuery, ActionRequest } from './types';

const path = (id: string) => `/actions/${encodeURIComponent(id)}`;

/**
 * Eylem istekleri (R1-R3 araç onayları). Onaylayan her zaman belirteçteki kullanıcıdır; yapay zekâ
 * ve talep eden onaylayamaz (403), süresi dolan istek 409 döner. Ham argümanlar API'de yoktur.
 */
export const actionsApi = {
  list: (query: ActionListQuery = {}, signal?: AbortSignal) =>
    api.get<ActionRequest[]>('/actions', { query, signal }),

  get: (id: string, signal?: AbortSignal) => api.get<ActionRequest>(path(id), { signal }),

  /** Onay kuralı sağlanınca istek onaylayanın kimliğiyle hemen yürütülür */
  approve: (id: string, comment = '') =>
    api.post<ActionRequest>(`${path(id)}/approve`, { comment }),

  reject: (id: string, comment = '') => api.post<ActionRequest>(`${path(id)}/reject`, { comment }),
};
