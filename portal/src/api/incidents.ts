import { api, requestWithMeta } from './client';
import type {
  AIDecision,
  Incident,
  IncidentCreate,
  IncidentListQuery,
  IncidentPatch,
  SLAAlert,
} from './types';

/** Olay uçları. Sürüm çakışmasında (409) ApiError.isConflict true olur. */
export const incidentsApi = {
  list: (query: IncidentListQuery = {}, signal?: AbortSignal) =>
    api.get<Incident[]>('/incidents', { query, signal }),

  /** Self-servis ve "kayıtlarım": caller == oturumdaki kullanıcı */
  listMine: (query: Omit<IncidentListQuery, 'mine'> = {}, signal?: AbortSignal) =>
    api.get<Incident[]>('/incidents', { query: { ...query, mine: true }, signal }),

  get: (id: string, signal?: AbortSignal) =>
    api.get<Incident>(`/incidents/${encodeURIComponent(id)}`, { signal }),

  /** requester için `caller` gönderilmez; sunucu belirteçteki kullanıcıyı yazar */
  create: (body: IncidentCreate) => api.post<Incident>('/incidents', body),

  /**
   * Kısmi güncelleme; `version` her zaman verilmeli (If-Match). Başkası kaydı değiştirdiyse
   * 409 döner: kaydı yeniden yükleyip kullanıcıya göstermek çağıranın işidir.
   */
  update: (id: string, version: number, patch: Omit<IncidentPatch, 'expected_version'>) =>
    api.patch<Incident>(`/incidents/${encodeURIComponent(id)}`, patch, { ifMatch: version }),

  /** Hizmet Masası ajanını çalıştırır; sonuç bir AIDecision'dır (uygulanmış olmayabilir). */
  triage: (id: string) => api.post<AIDecision>(`/incidents/${encodeURIComponent(id)}/triage`),

  decisions: (id: string, signal?: AbortSignal) =>
    api.get<AIDecision[]>(`/incidents/${encodeURIComponent(id)}/decisions`, { signal }),

  slaAlerts: (signal?: AbortSignal) => api.get<SLAAlert[]>('/incidents/sla/alerts', { signal }),

  /** ETag dahil ham yanıt (gerekirse) */
  getWithEtag: (id: string, signal?: AbortSignal) =>
    requestWithMeta<Incident>('GET', `/incidents/${encodeURIComponent(id)}`, { signal }),
};
