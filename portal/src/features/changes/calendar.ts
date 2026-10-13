/**
 * Değişiklik takvimi yardımcıları. Günler tarayıcının yerel saatine göre hesaplanır.
 * Çakışma işaretleri sunucu kuralının (services/changes.find_conflicts) özetidir ve yalnızca
 * uyarıdır; kesin denetim zamanlama/uygulama anında sunucuda yapılır.
 */
import type { Change, ChangeType, CIEnvironment, FreezeWindow } from '@/api/types';

export type CalendarMode = 'week' | 'month';

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

/** Pazartesi başlangıçlı hafta */
function startOfWeek(d: Date): Date {
  const day = startOfDay(d);
  const offset = (day.getDay() + 6) % 7;
  return addDays(day, -offset);
}

/** Görünen günler: hafta = 7 gün; ay = ayı kapsayan tam haftalar */
export function visibleDays(mode: CalendarMode, anchor: Date): Date[] {
  let first: Date;
  let count: number;
  if (mode === 'week') {
    first = startOfWeek(anchor);
    count = 7;
  } else {
    const monthStart = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const monthEnd = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    first = startOfWeek(monthStart);
    const last = addDays(startOfWeek(monthEnd), 6);
    count = Math.round((last.getTime() - first.getTime()) / DAY_MS) + 1;
  }
  return Array.from({ length: count }, (_, i) => addDays(first, i));
}

export function shiftAnchor(mode: CalendarMode, anchor: Date, step: number): Date {
  if (mode === 'week') return addDays(startOfDay(anchor), 7 * step);
  return new Date(anchor.getFullYear(), anchor.getMonth() + step, 1);
}

export function dayKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

const time = (iso: string | null) => (iso ? new Date(iso).getTime() : NaN);

/** Yarı açık aralıklar [aStart, aEnd) ve [bStart, bEnd) kesişiyor mu? */
export function overlaps(aStart: number, aEnd: number, bStart: number, bEnd: number): boolean {
  return aStart < bEnd && bStart < aEnd;
}

export function changesOnDay(day: Date, changes: readonly Change[]): Change[] {
  const start = startOfDay(day).getTime();
  const end = addDays(startOfDay(day), 1).getTime();
  return changes.filter((c) => {
    const s = time(c.planned_start);
    const e = time(c.planned_end);
    return Number.isFinite(s) && Number.isFinite(e) && overlaps(s, e, start, end);
  });
}

export function freezeOnDay(day: Date, windows: readonly FreezeWindow[]): FreezeWindow[] {
  const start = startOfDay(day).getTime();
  const end = addDays(startOfDay(day), 1).getTime();
  return windows.filter((w) => overlaps(time(w.start_at), time(w.end_at), start, end));
}

/** Aynı CI çakışmasında dikkate alınan durumlar (sunucu BLOCKING_STATES) */
const BLOCKING: readonly Change['state'][] = ['scheduled', 'implement'];

export interface PlannedWindow {
  start: Date;
  end: Date;
  ciIds: readonly string[];
  changeType: ChangeType;
  /** Etkilenen CI ortamları; boşsa her dondurma penceresi geçerli sayılır */
  environments?: readonly CIEnvironment[];
  /** Kendisiyle karşılaştırılmasın */
  excludeId?: string;
}

const freezeLabel = (w: FreezeWindow) => `Dondurma penceresi: ${w.reason || 'gerekçe yok'}`;

/** Bir pencere için çakışma gerekçeleri (Türkçe, gösterilebilir) */
export function windowConflicts(
  planned: PlannedWindow,
  changes: readonly Change[],
  windows: readonly FreezeWindow[],
): string[] {
  const s = planned.start.getTime();
  const e = planned.end.getTime();
  if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return [];
  const reasons: string[] = [];
  if (planned.changeType !== 'emergency') {
    const envs = planned.environments ?? [];
    for (const w of windows) {
      const envMatch =
        w.environments.length === 0 ||
        envs.length === 0 ||
        w.environments.some((env) => envs.includes(env));
      if (envMatch && overlaps(s, e, time(w.start_at), time(w.end_at)))
        reasons.push(freezeLabel(w));
    }
  }
  if (planned.ciIds.length > 0) {
    for (const other of changes) {
      if (other.id === planned.excludeId || !BLOCKING.includes(other.state)) continue;
      const shared = other.affected_ci_ids.some((id) => planned.ciIds.includes(id));
      if (shared && overlaps(s, e, time(other.planned_start), time(other.planned_end)))
        reasons.push(`Aynı CI üzerinde ${other.number}`);
    }
  }
  return reasons;
}

/** Takvimdeki her değişiklik için çakışma gerekçeleri (yalnızca çakışanlar) */
export function conflictMap(
  changes: readonly Change[],
  windows: readonly FreezeWindow[],
): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const c of changes) {
    if (!c.planned_start || !c.planned_end) continue;
    const reasons = windowConflicts(
      {
        start: new Date(c.planned_start),
        end: new Date(c.planned_end),
        ciIds: c.affected_ci_ids,
        changeType: c.change_type,
        excludeId: c.id,
      },
      changes,
      windows,
    );
    if (reasons.length) map.set(c.id, reasons);
  }
  return map;
}
