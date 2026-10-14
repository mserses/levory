import type { ChangeListQuery, ChangeState, ChangeType, RiskLevel } from '@/api/types';
import { CHANGE_STATE_LABELS, CHANGE_TYPE_LABELS, RISK_LEVEL_LABELS } from '@/lib/labels';
import type { CalendarMode } from './calendar';

export type ListView = 'list' | 'calendar';

export interface ChangeFilters {
  view: ListView;
  state: ChangeState | '';
  type: ChangeType | '';
  risk: RiskLevel | '';
  mine: boolean;
  /** CAB oyumu bekleyenler (cab/change_manager) */
  awaitingVote: boolean;
  calendarMode: CalendarMode;
  /** Takvim çapası (YYYY-MM-DD, yerel) */
  date: string;
}

const pick = <K extends string>(labels: Record<K, string>, value: string | null): K | '' =>
  value && value in labels ? (value as K) : '';

export function parseChangeParams(params: URLSearchParams): ChangeFilters {
  return {
    view: params.get('gorunum') === 'takvim' ? 'calendar' : 'list',
    state: pick(CHANGE_STATE_LABELS, params.get('durum')),
    type: pick(CHANGE_TYPE_LABELS, params.get('tur')),
    risk: pick(RISK_LEVEL_LABELS, params.get('risk')),
    mine: params.get('benim') === '1',
    awaitingVote: params.get('cab') === '1',
    calendarMode: params.get('takvim') === 'hafta' ? 'week' : 'month',
    date: /^\d{4}-\d{2}-\d{2}$/.test(params.get('tarih') ?? '') ? (params.get('tarih') ?? '') : '',
  };
}

export function toChangeParams(f: ChangeFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.view === 'calendar') p.set('gorunum', 'takvim');
  if (f.state) p.set('durum', f.state);
  if (f.type) p.set('tur', f.type);
  if (f.risk) p.set('risk', f.risk);
  if (f.mine) p.set('benim', '1');
  if (f.awaitingVote) p.set('cab', '1');
  if (f.view === 'calendar') p.set('takvim', f.calendarMode === 'week' ? 'hafta' : 'ay');
  if (f.date) p.set('tarih', f.date);
  return p;
}

export function toChangeQuery(f: ChangeFilters): ChangeListQuery {
  if (f.awaitingVote) return { state: 'authorize', limit: 200 };
  return {
    ...(f.state ? { state: f.state } : {}),
    ...(f.type ? { change_type: f.type } : {}),
    ...(f.mine ? { mine: true } : {}),
    limit: 200,
  };
}

/** YYYY-MM-DD → yerel gece yarısı (geçersizse bugün) */
export function anchorFromParam(value: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return new Date();
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}
