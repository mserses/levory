import { api } from './client';
import type {
  ServiceRequest,
  ServiceRequestCreate,
  ServiceRequestListQuery,
  TouchlessMetrics,
} from './types';

const path = (id: string) => `/requests/${encodeURIComponent(id)}`;

/** İş onayı kutusu sorgusu (sorgu anahtarında da aynısı kullanılır) */
export const AWAITING_MY_APPROVAL_QUERY = { awaiting_my_approval: true, limit: 200 } as const;

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

  /** İş onayımı bekleyenler (yönetici / onay grubu üyesi; backend `can_decide` ile süzer) */
  listAwaitingMyApproval: (signal?: AbortSignal) =>
    api.get<ServiceRequest[]>('/requests', {
      query: AWAITING_MY_APPROVAL_QUERY,
      signal,
    }),

  /** İş onayı: onaylanınca karşılama hemen başlar. Sürüm değiştiyse 409. */
  approve: (id: string, version: number, comment = '') =>
    api.post<ServiceRequest>(`${path(id)}/approve`, { comment, expected_version: version }),

  /** İş reddi (yorum zorunlu tutulur: talep edene gerekçe görünür) */
  reject: (id: string, version: number, comment: string) =>
    api.post<ServiceRequest>(`${path(id)}/reject`, { comment, expected_version: version }),

  /** Talep eden (kendi) ya da agent iptal eder */
  cancel: (id: string, version: number, note = '') =>
    api.post<ServiceRequest>(`${path(id)}/cancel`, { note, expected_version: version }),

  metrics: (signal?: AbortSignal) => api.get<TouchlessMetrics>('/requests/metrics', { signal }),
};
