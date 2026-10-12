/**
 * Onay kutusu için saf yardımcılar: AIDecision.output (serbest JSON) güvenli okunur,
 * mevcut ↔ önerilen alan karşılaştırması ve öncelik değişikliği sınıflandırması üretilir.
 */
import type { DecisionInboxItem, Incident, Priority } from '@/api/types';

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

const PRIORITIES: readonly Priority[] = ['P1', 'P2', 'P3', 'P4', 'P5'];

export function asPriority(v: unknown): Priority | null {
  return typeof v === 'string' && (PRIORITIES as readonly string[]).includes(v)
    ? (v as Priority)
    : null;
}

const rank = (p: Priority) => PRIORITIES.indexOf(p) + 1;
const isUrgent = (p: Priority) => p === 'P1' || p === 'P2';

export type PriorityChangeKind = 'urgent_raise' | 'urgent_downgrade' | 'raise' | 'lower' | 'same';

/** P1/P2'ye yükseltme ve P1/P2'den düşürme her zaman insan onayı ister (backend kuralı). */
export function classifyPriorityChange(current: Priority, proposed: Priority): PriorityChangeKind {
  if (current === proposed) return 'same';
  if (rank(proposed) < rank(current)) return isUrgent(proposed) ? 'urgent_raise' : 'raise';
  return isUrgent(current) ? 'urgent_downgrade' : 'lower';
}

// Alan adları ve değer gösterimi tüm ekranlarda ortak (lib/fields)
export { FIELD_LABELS, displayValue, fieldLabel } from '@/lib/fields';

export interface ParsedProposal {
  /** Onaylanınca olaya yazılacak alanlar (output.proposed) */
  proposed: Record<string, string>;
  /** Karar anındaki öncelik */
  decisionPriority: Priority | null;
  proposedPriority: Priority | null;
  appliedFields: string[];
  policyReason: string | null;
  suggestedResolution: string | null;
}

export function parseProposal(output: DecisionInboxItem['output']): ParsedProposal {
  const proposedRaw = isRecord(output.proposed) ? output.proposed : {};
  const proposed: Record<string, string> = {};
  for (const [key, value] of Object.entries(proposedRaw)) {
    if (typeof value === 'string' && key !== 'priority') proposed[key] = value;
  }
  const reason = output.policy_reason;
  const resolution = output.suggested_resolution;
  return {
    proposed,
    decisionPriority: asPriority(output.current_priority),
    // Eski/kısa çıktılarda öncelik doğrudan proposed.priority olabilir
    proposedPriority: asPriority(output.proposed_priority) ?? asPriority(proposedRaw.priority),
    appliedFields: strList(output.applied_fields),
    policyReason: typeof reason === 'string' && reason.trim() ? reason : null,
    suggestedResolution: typeof resolution === 'string' && resolution.trim() ? resolution : null,
  };
}

export type RowState = 'change' | 'same' | 'unknown';

export interface DiffRow {
  field: string;
  current: string | null;
  proposed: string;
  state: RowState;
}

const FIELD_ORDER = ['kind', 'category', 'service', 'impact', 'urgency', 'assignment_group'];

/**
 * "Mevcut" sütunu kaydın ŞU ANKİ hâlidir (incident yüklenemezse bilinmiyor).
 * Öncelik satırı inbox'taki canlı öncelikten (incident_priority) beslenir.
 */
export function buildDiff(
  item: Pick<DecisionInboxItem, 'incident_priority'>,
  parsed: ParsedProposal,
  incident: Incident | undefined,
): DiffRow[] {
  const keys = Object.keys(parsed.proposed).sort((a, b) => {
    const ia = FIELD_ORDER.indexOf(a);
    const ib = FIELD_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
  });
  const rows: DiffRow[] = keys.map((field) => {
    const proposed = parsed.proposed[field] ?? '';
    if (!incident) return { field, current: null, proposed, state: 'unknown' };
    const raw = (incident as unknown as Record<string, unknown>)[field];
    const current = typeof raw === 'string' ? raw : null;
    return { field, current, proposed, state: current === proposed ? 'same' : 'change' };
  });
  const currentPriority = incident?.priority ?? item.incident_priority ?? parsed.decisionPriority;
  if (parsed.proposedPriority) {
    rows.push({
      field: 'priority',
      current: currentPriority,
      proposed: parsed.proposedPriority,
      state: currentPriority
        ? currentPriority === parsed.proposedPriority
          ? 'same'
          : 'change'
        : 'unknown',
    });
  }
  return rows;
}

/** Öncelik değişikliği (canlı öncelik → önerilen); karşılaştırılamıyorsa null */
export function priorityChange(
  item: Pick<DecisionInboxItem, 'incident_priority'>,
  parsed: ParsedProposal,
): { current: Priority; proposed: Priority; kind: PriorityChangeKind } | null {
  const current = item.incident_priority ?? parsed.decisionPriority;
  const proposed = parsed.proposedPriority;
  if (!current || !proposed) return null;
  return { current, proposed, kind: classifyPriorityChange(current, proposed) };
}

/** Karar verildikten sonra kayıt değişti mi? (onay büyük olasılıkla 409 döner) */
export function isLikelyStale(
  item: Pick<DecisionInboxItem, 'incident_version' | 'incident_current_version'>,
): boolean {
  return (
    item.incident_version !== null &&
    item.incident_current_version !== null &&
    item.incident_version !== item.incident_current_version
  );
}

export const STALE_MESSAGE = 'Kayıt öneriden sonra değişti; yeniden triage gerekli.';

/** Senkron çakışması yükü (SyncEvent.payload) — sunucu tarafında serbest JSON */
export interface ConflictPayload {
  incidentId: string | null;
  localVersion: number | null;
  lastSyncedLocalVersion: number | null;
  remoteUpdatedAt: string | null;
  lastSyncedRemoteUpdated: string | null;
  local: Record<string, string | null>;
  remote: Record<string, string | null>;
}

function stringMap(v: unknown): Record<string, string | null> {
  if (!isRecord(v)) return {};
  const out: Record<string, string | null> = {};
  for (const [k, val] of Object.entries(v)) {
    out[k] =
      val === null || val === undefined
        ? null
        : typeof val === 'string'
          ? val
          : JSON.stringify(val);
  }
  return out;
}

const num = (v: unknown): number | null => (typeof v === 'number' ? v : null);
const strOrNull = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

export function parseConflictPayload(payload: Record<string, unknown>): ConflictPayload {
  return {
    incidentId: strOrNull(payload.incident_id),
    localVersion: num(payload.local_version),
    lastSyncedLocalVersion: num(payload.last_synced_local_version),
    remoteUpdatedAt: strOrNull(payload.remote_updated_at),
    lastSyncedRemoteUpdated: strOrNull(payload.last_synced_remote_updated),
    local: stringMap(payload.local),
    remote: stringMap(payload.remote),
  };
}

/** Uzak anlık görüntüde karşılaştırma dışı (kimlik/zaman) alanlar */
const REMOTE_META_FIELDS = new Set(['external_id', 'number', 'updated_at', 'raw']);
const CONFLICT_FIELD_ORDER = [
  'title',
  'description',
  'status',
  'priority',
  'impact',
  'urgency',
  'assignee',
  'resolution',
];

export interface ConflictRow {
  field: string;
  local: string | null;
  remote: string | null;
  /** İki tarafta da değer varsa ve farklıysa; bir tarafta alan yoksa karşılaştırılmaz */
  differs: boolean;
  localMissing: boolean;
  remoteMissing: boolean;
}

const norm = (v: string | null) => (v ?? '').trim();

export function buildConflictRows(parsed: ConflictPayload): ConflictRow[] {
  const keys = new Set<string>();
  for (const k of Object.keys(parsed.local)) keys.add(k);
  for (const k of Object.keys(parsed.remote)) if (!REMOTE_META_FIELDS.has(k)) keys.add(k);
  return [...keys]
    .sort((a, b) => {
      const ia = CONFLICT_FIELD_ORDER.indexOf(a);
      const ib = CONFLICT_FIELD_ORDER.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
    })
    .map((field) => {
      const localMissing = !(field in parsed.local);
      const remoteMissing = !(field in parsed.remote);
      const local = parsed.local[field] ?? null;
      const remote = parsed.remote[field] ?? null;
      return {
        field,
        local,
        remote,
        localMissing,
        remoteMissing,
        differs: !localMissing && !remoteMissing && norm(local) !== norm(remote),
      };
    });
}

export { SYSTEM_LABELS, systemLabel } from '@/lib/labels';
