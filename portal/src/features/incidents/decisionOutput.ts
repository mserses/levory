/**
 * AIDecision.output serbest biçimli bir JSON'dur (`{[key: string]: unknown}`); burada tip
 * korumalarıyla güvenli biçimde okunur. Bilinmeyen/eksik alanlar yok sayılır.
 */
import type { AIDecision, Incident, Priority } from '@/api/types';

export type ProposableField =
  | 'kind'
  | 'category'
  | 'service'
  | 'impact'
  | 'urgency'
  | 'assignment_group';

export const PROPOSABLE_FIELDS: readonly ProposableField[] = [
  'kind',
  'category',
  'service',
  'impact',
  'urgency',
  'assignment_group',
];

// Alan adları ve değer gösterimi tüm ekranlarda ortak (lib/fields)
export { FIELD_LABELS, displayValue, fieldLabel } from '@/lib/fields';

export interface ParsedDecisionOutput {
  proposed: Partial<Record<ProposableField, string>>;
  appliedFields: string[];
  pendingFields: string[];
  droppedKbIds: string[];
  offTaxonomy: { field: string; value: string }[];
  currentPriority: Priority | null;
  proposedPriority: Priority | null;
  suggestedResolution: string | null;
  policyReason: string | null;
  error: string | null;
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

const PRIORITIES: readonly string[] = ['P1', 'P2', 'P3', 'P4', 'P5'];
const priority = (v: unknown): Priority | null =>
  typeof v === 'string' && PRIORITIES.includes(v) ? (v as Priority) : null;

const ERROR_TEXT: Record<string, string> = {
  invalid_output: 'Yapay zekâ çıktısı beklenen şemaya uymadı.',
};

export function parseDecisionOutput(output: AIDecision['output']): ParsedDecisionOutput {
  const proposedRaw = isRecord(output.proposed) ? output.proposed : {};
  const proposed: Partial<Record<ProposableField, string>> = {};
  for (const field of PROPOSABLE_FIELDS) {
    const value = proposedRaw[field];
    if (typeof value === 'string') proposed[field] = value;
  }
  const offRaw = isRecord(output.off_taxonomy) ? output.off_taxonomy : {};
  const offTaxonomy = Object.entries(offRaw)
    .filter((e): e is [string, string] => typeof e[1] === 'string')
    .map(([field, value]) => ({ field, value }));
  const errorCode = str(output.error);
  return {
    proposed,
    appliedFields: strList(output.applied_fields),
    pendingFields: strList(output.pending_fields),
    droppedKbIds: strList(output.dropped_kb_ids),
    offTaxonomy,
    currentPriority: priority(output.current_priority),
    proposedPriority: priority(output.proposed_priority),
    suggestedResolution: str(output.suggested_resolution),
    policyReason: str(output.policy_reason),
    error: errorCode ? (ERROR_TEXT[errorCode] ?? errorCode) : null,
  };
}

export type DiffState = 'applied' | 'pending' | 'same';

export interface DiffRow {
  field: ProposableField | 'priority';
  current: string | null;
  proposed: string;
  state: DiffState;
}

/**
 * Önerilen ve kaydın güncel değerlerini karşılaştırır. "Mevcut" sütunu kaydın ŞU ANKİ
 * hâlidir (öneri uygulandıysa öneriyle aynı görünür; durum sütunu bunu açıklar).
 */
export function buildDiff(parsed: ParsedDecisionOutput, incident: Incident): DiffRow[] {
  const rows: DiffRow[] = [];
  for (const field of PROPOSABLE_FIELDS) {
    const proposed = parsed.proposed[field];
    if (proposed === undefined) continue;
    const current = incident[field];
    const state: DiffState = parsed.appliedFields.includes(field)
      ? 'applied'
      : current === proposed
        ? 'same'
        : 'pending';
    rows.push({ field, current, proposed, state });
  }
  if (parsed.proposedPriority && parsed.currentPriority) {
    const applied =
      parsed.appliedFields.includes('impact') || parsed.appliedFields.includes('urgency');
    const state: DiffState =
      parsed.proposedPriority === parsed.currentPriority
        ? 'same'
        : applied && incident.priority === parsed.proposedPriority
          ? 'applied'
          : incident.priority === parsed.proposedPriority
            ? 'same'
            : 'pending';
    rows.push({
      field: 'priority',
      current: incident.priority,
      proposed: parsed.proposedPriority,
      state,
    });
  }
  return rows;
}
