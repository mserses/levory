/**
 * Sürekli iyileştirme (CSI) kaydı kuralları (backend `services/improvements.py`, docs/faz4.md).
 * Sunucu her adımı yine denetler (403 / 409 / 422); burada yalnızca gösterilecek düğmeler seçilir.
 *
 * idea → (approver) onay → approved → (agent/approver) başlat → in_progress → uygulandı işareti →
 * ölçüm penceresi dolunca ölç → measured → (approver) kapat. idea/approved/in_progress → red
 * (approver, gerekçe zorunlu). KPI'sız kayıt in_progress'ten kapanış notuyla kapanır.
 */
import type { Improvement, KPIDefinition } from '@/api/types';
import { hasAnyRole, type Role } from '@/auth/roles';
import { formatKpiValue } from '@/features/kpi/model';

export type ImprovementStep = 'approve' | 'reject' | 'start' | 'implemented' | 'measure' | 'close';

export const STEP_LABELS: Record<ImprovementStep, string> = {
  approve: 'Onayla',
  reject: 'Reddet',
  start: 'Uygulamaya başla',
  implemented: 'Uygulandı olarak işaretle',
  measure: 'Ölç',
  close: 'Kapat',
};

/** Backend rolleri: onay/red/kapatma `ApproverUser`, diğerleri `ReaderUser` (agent/approver) */
export const STEP_ROLES: Record<ImprovementStep, readonly Role[]> = {
  approve: ['approver'],
  reject: ['approver'],
  close: ['approver'],
  start: ['agent', 'approver'],
  implemented: ['agent', 'approver'],
  measure: ['agent', 'approver'],
};

function stepsFor(imp: Improvement): ImprovementStep[] {
  switch (imp.state) {
    case 'idea':
      return ['approve', 'reject'];
    case 'approved':
      return ['start', 'reject'];
    case 'in_progress': {
      const steps: ImprovementStep[] = [];
      if (imp.kpi_key) {
        if (imp.implemented_at === null) steps.push('implemented');
        else steps.push('measure');
      } else steps.push('close');
      steps.push('reject');
      return steps;
    }
    case 'measured':
      return ['close', 'measure'];
    default:
      return [];
  }
}

export function availableSteps(imp: Improvement, roles: readonly string[]): ImprovementStep[] {
  return stepsFor(imp).filter((s) => hasAnyRole(roles, STEP_ROLES[s]));
}

export type NoteNeed = 'required' | 'optional' | 'none';

export function stepNote(step: ImprovementStep, imp: Improvement): NoteNeed {
  if (step === 'reject') return 'required';
  if (step === 'close')
    return imp.state === 'in_progress' && !imp.kpi_key ? 'required' : 'optional';
  if (step === 'approve' || step === 'start') return 'optional';
  return 'none';
}

const DAY_MS = 86_400_000;

/** Ölçüm penceresinin dolacağı an (`implemented_at + measure_days`); işaret yoksa null */
export function measureDueAt(imp: Improvement): Date | null {
  if (!imp.implemented_at) return null;
  return new Date(new Date(imp.implemented_at).getTime() + imp.measure_days * DAY_MS);
}

export type KpiUnit = KPIDefinition['unit'];

export function unitFor(imp: Improvement, defs: readonly KPIDefinition[] | undefined): KpiUnit {
  return defs?.find((d) => d.key === imp.kpi_key)?.unit ?? 'count';
}

export const valueText = (value: number | null, unit: KpiUnit) => formatKpiValue(value, unit);

export interface TimelineEntry {
  at: string;
  label: string;
  actor: string | null;
  note: string | null;
}

/** Kayıt izlerinden (onay/başlatma/uygulama/ölçüm/kapanış) zaman çizelgesi, eskiden yeniye */
export function timeline(imp: Improvement): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    { at: imp.created_at, label: 'Önerildi', actor: imp.detected_by, note: null },
  ];
  if (imp.approved_at) {
    entries.push({ at: imp.approved_at, label: 'Onaylandı', actor: imp.approved_by, note: null });
  }
  if (imp.started_at) {
    entries.push({ at: imp.started_at, label: 'Uygulamaya başlandı', actor: null, note: null });
  }
  if (imp.implemented_at) {
    entries.push({ at: imp.implemented_at, label: 'Uygulama bitti', actor: null, note: null });
  }
  if (imp.measured_at) {
    entries.push({ at: imp.measured_at, label: 'Ölçüldü', actor: null, note: null });
  }
  if (imp.closed_at) {
    entries.push({
      at: imp.closed_at,
      label: 'Kapandı',
      actor: imp.closed_by,
      note: imp.resolution,
    });
  } else if (imp.state === 'rejected') {
    entries.push({ at: imp.updated_at, label: 'Reddedildi', actor: null, note: imp.resolution });
  }
  return entries.sort((a, b) => a.at.localeCompare(b.at));
}

/** Kanıt değeri düz metin (sunucu/yapay zekâ verisi; HTML olarak yorumlanmaz) */
export function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value) && value.every((v) => typeof v === 'string' || typeof v === 'number')) {
    return value.join(', ');
  }
  const text = JSON.stringify(value);
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

interface MeasurementSide {
  value: number | null;
  count?: number;
  window?: { start: string; end: string };
}

export interface Measurement {
  before: MeasurementSide | null;
  after: MeasurementSide | null;
  deltaPct: number | null;
  improved: boolean | null;
}

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const obj = (v: unknown) =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

function side(v: unknown): MeasurementSide | null {
  const o = obj(v);
  if (!o) return null;
  const w = obj(o.window);
  const count = num(o.count);
  return {
    value: num(o.value),
    ...(count !== null ? { count } : {}),
    ...(w && typeof w.start === 'string' && typeof w.end === 'string'
      ? { window: { start: w.start, end: w.end } }
      : {}),
  };
}

/** `measurement` JSON'unu (önce/sonra) güvenli biçimde okur */
export function parseMeasurement(imp: Improvement): Measurement | null {
  const m = obj(imp.measurement);
  if (!m) return null;
  return {
    before: side(m.before),
    after: side(m.after),
    deltaPct: num(m.delta_pct),
    improved: typeof m.improved === 'boolean' ? m.improved : null,
  };
}
