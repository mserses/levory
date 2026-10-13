import { api, requestWithMeta } from './client';
import type {
  AgentResult,
  AgentResults,
  Problem,
  ProblemCreate,
  ProblemListQuery,
  ProblemState,
  ProblemUpdate,
  ProposeFix,
  RCA,
  Trends,
} from './types';

const path = (id: string) => `/problems/${encodeURIComponent(id)}`;

/**
 * Sorun yönetimi uçları. Yazmalar sürümlüdür (`If-Match`; çakışma 409). Yapay zekâ uçları
 * (`draftRca`, `proposeFix`, `proposeFromTrends`) araç boru hattından geçer: politika izin
 * vermezse sonuç `pending_approval` ve `action_request_id` taşır (onay /onaylar).
 */
export const problemsApi = {
  list: (query: ProblemListQuery = {}, signal?: AbortSignal) =>
    api.get<Problem[]>('/problems', { query, signal }),

  get: (id: string, signal?: AbortSignal) => api.get<Problem>(path(id), { signal }),

  getWithEtag: (id: string, signal?: AbortSignal) =>
    requestWithMeta<Problem>('GET', path(id), { signal }),

  create: (body: ProblemCreate) => api.post<Problem>('/problems', body),

  update: (id: string, version: number, patch: ProblemUpdate) =>
    api.patch<Problem>(path(id), patch, { ifMatch: version }),

  transition: (id: string, version: number, toState: ProblemState, note?: string) =>
    api.post<Problem>(
      `${path(id)}/transition`,
      { to_state: toState, note: note ?? null },
      { ifMatch: version },
    ),

  unlinkIncident: (id: string, incidentId: string) =>
    api.delete<Problem>(`${path(id)}/incidents/${encodeURIComponent(incidentId)}`),

  /** Kesin RCA (insan) */
  recordRca: (id: string, version: number, rca: RCA) =>
    api.put<Problem>(`${path(id)}/rca`, { rca }, { ifMatch: version }),

  /** Yapay zekâ taslağını kesin RCA olarak kabul eder (insan) */
  acceptRcaDraft: (id: string) => api.post<Problem>(`${path(id)}/rca/accept`),

  /** Yapay zekâ 5 Neden taslağı (yalnızca `rca_draft` yazılır) */
  draftRca: (id: string) => api.post<AgentResult>(`${path(id)}/rca/draft`),

  /** Kalıcı çözüm için taslak RFC önerisi */
  proposeFix: (id: string, body: ProposeFix) => api.post<AgentResult>(`${path(id)}/fix`, body),

  /** Geçici çözümü bilinen hata (KEDB) makalesi olarak yayımlar (insan) */
  publishKnownError: (id: string) => api.post<Problem>(`${path(id)}/known-error/publish`),

  trends: (windowDays: number) => api.post<Trends>('/problems/trends', { window_days: windowDays }),

  proposeFromTrends: (windowDays: number) =>
    api.post<AgentResults>('/problems/trends/propose', { window_days: windowDays }),
};
