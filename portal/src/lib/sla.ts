/**
 * SLA görünüm hesabı (yalnızca gösterim içindir; kaynak doğruluk backend'dedir).
 * Backend kuralları: arayan/tedarikçi beklemesinde saat durur (`sla_paused_at` dolu),
 * %75 eşiği "risk altında", vade geçince "ihlal".
 */
import type { Incident } from '@/api/types';
import { toDate } from './format';

export const AT_RISK_RATIO = 0.75;

export type SlaState = 'none' | 'on_track' | 'at_risk' | 'breached' | 'paused' | 'met' | 'missed';
export type SlaMetric = 'response' | 'resolve';

export interface SlaView {
  state: SlaState;
  metric: SlaMetric | null;
  dueAt: Date | null;
  /** Vadeye kalan saniye (geçtiyse negatif); duraklatılmış/karşılanmışsa null */
  remainingSeconds: number | null;
}

export type SlaFields = Pick<
  Incident,
  | 'status'
  | 'created_at'
  | 'response_due_at'
  | 'resolve_due_at'
  | 'responded_at'
  | 'resolved_at'
  | 'sla_paused_at'
>;

const STOPPED: ReadonlySet<Incident['status']> = new Set(['resolved', 'closed', 'cancelled']);

export function computeSla(incident: SlaFields, now: Date = new Date()): SlaView {
  const created = toDate(incident.created_at);
  const responseDue = toDate(incident.response_due_at);
  const resolveDue = toDate(incident.resolve_due_at);
  const resolvedAt = toDate(incident.resolved_at);

  if (STOPPED.has(incident.status)) {
    if (incident.status === 'cancelled' || !resolveDue) {
      return { state: 'none', metric: null, dueAt: null, remainingSeconds: null };
    }
    const met = resolvedAt ? resolvedAt <= resolveDue : true;
    return {
      state: met ? 'met' : 'missed',
      metric: 'resolve',
      dueAt: resolveDue,
      remainingSeconds: null,
    };
  }

  const metric: SlaMetric | null =
    !incident.responded_at && responseDue ? 'response' : resolveDue ? 'resolve' : null;
  const dueAt = metric === 'response' ? responseDue : metric === 'resolve' ? resolveDue : null;
  if (!metric || !dueAt)
    return { state: 'none', metric: null, dueAt: null, remainingSeconds: null };

  if (incident.sla_paused_at) {
    return { state: 'paused', metric, dueAt, remainingSeconds: null };
  }

  const remainingSeconds = Math.round((dueAt.getTime() - now.getTime()) / 1000);
  if (remainingSeconds < 0) return { state: 'breached', metric, dueAt, remainingSeconds };
  if (created) {
    const total = dueAt.getTime() - created.getTime();
    const elapsed = now.getTime() - created.getTime();
    if (total > 0 && elapsed / total >= AT_RISK_RATIO) {
      return { state: 'at_risk', metric, dueAt, remainingSeconds };
    }
  }
  return { state: 'on_track', metric, dueAt, remainingSeconds };
}
