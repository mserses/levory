import { api } from './client';
import type {
  ServiceRequest,
  ServiceRequestCreate,
  ServiceRequestListQuery,
  TouchlessMetrics,
} from './types';

const path = (id: string) => `/requests/${encodeURIComponent(id)}`;

/**
 * Hizmet talepleri (REQ…). Açılışta iş onayı gerekmiyorsa karşılama hemen başlar; R2/R3 adımlar
 * eylem isteği (ActionRequest) açar ve talep o adımda bekler.
 */
export const requestsApi = {
  list: (query: ServiceRequestListQuery = {}, signal?: AbortSignal) =>
    api.get<ServiceRequest[]>('/requests', { query, signal }),

  /** Talep edeni oturumdaki kullanıcı olanlar */
  listMine: (query: Omit<ServiceRequestListQuery, 'mine'> = {}, signal?: AbortSignal) =>
    api.get<ServiceRequest[]>('/requests', { query: { ...query, mine: true }, signal }),

  get: (id: string, signal?: AbortSignal) => api.get<ServiceRequest>(path(id), { signal }),

  create: (body: ServiceRequestCreate) => api.post<ServiceRequest>('/requests', body),

  /** Talep eden (kendi) ya da agent iptal eder */
  cancel: (id: string, version: number, note = '') =>
    api.post<ServiceRequest>(`${path(id)}/cancel`, { note, expected_version: version }),

  metrics: (signal?: AbortSignal) => api.get<TouchlessMetrics>('/requests/metrics', { signal }),
};
