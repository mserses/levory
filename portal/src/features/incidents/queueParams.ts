/** Kuyruk filtreleri URL'de tutulur (paylaşılabilir/yer imi). */
import type { IncidentListQuery, IncidentStatus, Priority } from '@/api/types';
import { PRIORITY_LABELS, STATUS_LABELS } from '@/lib/labels';

export const PAGE_SIZE = 25;

export type QueueSort = 'sla' | 'newest';

export interface QueueFilters {
  status: IncidentStatus | null;
  priority: Priority | null;
  assignee: string;
  mine: boolean;
  sort: QueueSort;
  /** 1 tabanlı sayfa numarası */
  page: number;
}

export const DEFAULT_FILTERS: QueueFilters = {
  status: null,
  priority: null,
  assignee: '',
  mine: false,
  sort: 'sla',
  page: 1,
};

function oneOf<T extends string>(map: Record<T, string>, value: string | null): T | null {
  return value !== null && Object.prototype.hasOwnProperty.call(map, value) ? (value as T) : null;
}

export function parseQueueParams(params: URLSearchParams): QueueFilters {
  const page = Number.parseInt(params.get('sayfa') ?? '1', 10);
  return {
    status: oneOf(STATUS_LABELS, params.get('durum')),
    priority: oneOf(PRIORITY_LABELS, params.get('oncelik')),
    assignee: (params.get('atanan') ?? '').trim(),
    mine: params.get('benim') === '1',
    sort: params.get('sirala') === 'yeni' ? 'newest' : 'sla',
    page: Number.isFinite(page) && page >= 1 ? page : 1,
  };
}

export function toSearchParams(filters: QueueFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (filters.status) p.set('durum', filters.status);
  if (filters.priority) p.set('oncelik', filters.priority);
  if (filters.assignee) p.set('atanan', filters.assignee);
  if (filters.mine) p.set('benim', '1');
  if (filters.sort === 'newest') p.set('sirala', 'yeni');
  if (filters.page > 1) p.set('sayfa', String(filters.page));
  return p;
}

/**
 * API sorgusu. Bir sonraki sayfanın varlığını anlamak için bir fazla kayıt istenir
 * (API toplam sayı döndürmez).
 */
export function toApiQuery(filters: QueueFilters): IncidentListQuery {
  const q: IncidentListQuery = {
    limit: PAGE_SIZE + 1,
    offset: (filters.page - 1) * PAGE_SIZE,
  };
  if (filters.status) q.status = filters.status;
  if (filters.priority) q.priority = filters.priority;
  if (filters.assignee) q.assignee = filters.assignee;
  if (filters.mine) q.mine = true;
  return q;
}

export function hasActiveFilters(f: QueueFilters): boolean {
  return f.status !== null || f.priority !== null || f.assignee !== '' || f.mine;
}
