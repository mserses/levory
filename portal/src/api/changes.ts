import { api, requestWithMeta } from './client';
import { isApiError } from './errors';
import type {
  AIDecision,
  Change,
  ChangeApproval,
  ChangeApprovalCreate,
  ChangeApprovalList,
  ChangeCalendar,
  ChangeCreate,
  ChangeListQuery,
  ChangePatch,
  ChangeTemplate,
  ChangeTransition,
  FreezeWindow,
  ImpactAnalysis,
  ScheduleConflict,
} from './types';

const path = (id: string) => `/changes/${encodeURIComponent(id)}`;

/**
 * Değişiklik yönetimi (Change Enablement + CAB) uçları. Yetki ve durum kuralları backend'dedir;
 * sürüm çakışması ve zamanlama çakışması 409 döner (`scheduleConflictsOf` ile ayırt edilir).
 */
export const changesApi = {
  list: (query: ChangeListQuery = {}, signal?: AbortSignal) =>
    api.get<Change[]>('/changes', { query, signal }),

  get: (id: string, signal?: AbortSignal) => api.get<Change>(path(id), { signal }),

  getWithEtag: (id: string, signal?: AbortSignal) =>
    requestWithMeta<Change>('GET', path(id), { signal }),

  create: (body: ChangeCreate) => api.post<Change>('/changes', body),

  /** Yalnızca taslak; `version` If-Match olarak gider */
  update: (id: string, version: number, patch: Omit<ChangePatch, 'expected_version'>) =>
    api.patch<Change>(path(id), patch, { ifMatch: version }),

  /** Durum geçişi; `version` If-Match olarak gider (iyimser kilit) */
  transition: (id: string, version: number, body: ChangeTransition) =>
    api.post<Change>(`${path(id)}/transition`, body, { ifMatch: version }),

  approvals: (id: string, signal?: AbortSignal) =>
    api.get<ChangeApprovalList>(`${path(id)}/approvals`, { signal }),

  /** CAB/ECAB kararı; onaylayan her zaman belirteçteki kullanıcıdır */
  vote: (id: string, body: ChangeApprovalCreate) =>
    api.post<ChangeApproval>(`${path(id)}/approvals`, body),

  conflicts: (id: string, signal?: AbortSignal) =>
    api.get<ScheduleConflict[]>(`${path(id)}/conflicts`, { signal }),

  impact: (id: string, signal?: AbortSignal) =>
    api.get<ImpactAnalysis>(`${path(id)}/impact`, { signal }),

  decisions: (id: string, signal?: AbortSignal) =>
    api.get<AIDecision[]>(`${path(id)}/decisions`, { signal }),

  /** Yapay zekâ risk değerlendirmesi: yalnızca risk alanları, politika izin verirse */
  assess: (id: string) => api.post<AIDecision>(`${path(id)}/assess`),

  /** Riski düşüren yapay zekâ önerisini onaylar (change_manager); bayatsa 409 */
  approveDecision: (decisionId: string) =>
    api.post<AIDecision>(`/changes/decisions/${encodeURIComponent(decisionId)}/approve`),

  calendar: (from: Date, to: Date, signal?: AbortSignal) =>
    api.get<ChangeCalendar>('/changes/calendar', {
      query: { from: from.toISOString(), to: to.toISOString() },
      signal,
    }),

  templates: (active?: boolean, signal?: AbortSignal) =>
    api.get<ChangeTemplate[]>('/changes/templates', { query: { active }, signal }),

  freezeWindows: (from?: Date, to?: Date, signal?: AbortSignal) =>
    api.get<FreezeWindow[]>('/changes/freeze-windows', {
      query: { from: from?.toISOString(), to: to?.toISOString() },
      signal,
    }),
};

function isConflictList(value: unknown): value is ScheduleConflict[] {
  return (
    Array.isArray(value) &&
    value.every(
      (c) => typeof c === 'object' && c !== null && 'kind' in c && 'start' in c && 'end' in c,
    )
  );
}

/**
 * 409 zamanlama çakışması gövdesi `{detail: {message, conflicts}}`; sürüm çakışmasında
 * `detail` düz metindir. Zamanlama çakışmasıysa çakışma listesini, değilse null döner.
 */
export function scheduleConflictsOf(error: unknown): ScheduleConflict[] | null {
  if (!isApiError(error) || !error.isConflict) return null;
  const body = error.body as { detail?: { conflicts?: unknown } } | undefined;
  const conflicts = body?.detail?.conflicts;
  return isConflictList(conflicts) ? conflicts : null;
}
