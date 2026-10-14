import { api } from './client';
import type { AIDecision, DecisionInboxItem, DecisionInboxQuery } from './types';

export const decisionsApi = {
  /** Olaylar arası onay kutusu (approver/admin); outcome boşsa needs_approval+suggested+escalated */
  inbox: (query: DecisionInboxQuery = {}, signal?: AbortSignal) =>
    api.get<DecisionInboxItem[]>('/incidents/decisions', { query, signal }),

  /**
   * Kararı onaylar ve olaya uygular. Karar verildikten sonra olay değiştiyse 409
   * ("yeniden triage gerekli") döner: `ApiError.needsRetriage`.
   */
  approve: (decisionId: string) =>
    api.post<AIDecision>(`/incidents/decisions/${encodeURIComponent(decisionId)}/approve`),
};
