/**
 * Backend iş kurallarının ekrandaki yansıması (yalnızca kullanıcıyı yönlendirmek içindir;
 * asıl doğrulama backend'dedir: services/incidents.py, domain/priority.py).
 */
import type { Impact, IncidentStatus, OnHoldReason, Priority, Urgency } from '@/api/types';
import { SLA_PAUSING_REASONS } from '@/lib/labels';

/** İzin verilen durum geçişleri (ITIL 4 olay yaşam döngüsü; backend ALLOWED_TRANSITIONS) */
export const ALLOWED_TRANSITIONS: Record<IncidentStatus, readonly IncidentStatus[]> = {
  new: ['assigned', 'in_progress', 'on_hold', 'resolved', 'cancelled'],
  assigned: ['in_progress', 'on_hold', 'resolved', 'cancelled'],
  in_progress: ['assigned', 'on_hold', 'resolved', 'cancelled'],
  on_hold: ['assigned', 'in_progress', 'resolved', 'cancelled'],
  resolved: ['in_progress', 'closed'],
  closed: [],
  cancelled: [],
};

/** Kapalı/iptal kayıtlar değiştirilemez */
export function isFinalStatus(status: IncidentStatus): boolean {
  return ALLOWED_TRANSITIONS[status].length === 0;
}

export function pausesSla(reason: OnHoldReason | null | undefined): boolean {
  return reason ? SLA_PAUSING_REASONS.includes(reason) : false;
}

/** Etki x aciliyet → öncelik matrisi (backend domain/priority.py ile aynı) */
const MATRIX: Record<Impact, Record<Urgency, Priority>> = {
  high: { high: 'P1', medium: 'P2', low: 'P3' },
  medium: { high: 'P2', medium: 'P3', low: 'P4' },
  low: { high: 'P3', medium: 'P4', low: 'P5' },
};

export function calculatePriority(impact: Impact, urgency: Urgency): Priority {
  return MATRIX[impact][urgency];
}

const URGENT: ReadonlySet<Priority> = new Set(['P1', 'P2']);

/** P1/P2'ye yükseltme ya da P1/P2'den düşürme (yapay zekâ için daima onay gerektirir) */
export function isUrgentPriorityChange(from: Priority, to: Priority): boolean {
  return from !== to && (URGENT.has(from) || URGENT.has(to));
}
