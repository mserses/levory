/**
 * Hizmet talebi karşılama planından adım zaman çizelgesi üretir (backend `services/requests.py`):
 * `plan[i]` = {tool, title, args, when} | {manual: true, title}; `step_results[]` = {index, title,
 * tool, status: executed|pending_approval|waiting_manual|skipped|failed, actor?, at,
 * risk_class?, action_request_id?, error?: {code, message}, note?}.
 * Sonucu olmayan adım: talep karşılanıyorsa ve sıradaysa "Sırada", talep bittiyse "Çalışmadı".
 */
import type { LinkedAction, RiskClass, ServiceRequest } from '@/api/types';

export type StepState =
  | 'executed'
  | 'pending_approval'
  | 'waiting_manual'
  | 'skipped'
  | 'failed'
  | 'queued'
  | 'not_run';

export interface TimelineStep {
  index: number;
  title: string;
  tool: string | null;
  manual: boolean;
  state: StepState;
  actor: string | null;
  at: string | null;
  riskClass: RiskClass | null;
  action: LinkedAction | null;
  actionRequestId: string | null;
  error: string | null;
  note: string | null;
}

export const STEP_STATE_LABELS: Record<StepState, string> = {
  executed: 'Tamamlandı',
  pending_approval: 'Onay bekliyor',
  waiting_manual: 'BT ekibi el ile yapacak',
  skipped: 'Atlandı (gerekmedi)',
  failed: 'Başarısız',
  queued: 'Sırada',
  not_run: 'Çalışmadı',
};

const KNOWN: ReadonlySet<string> = new Set([
  'executed',
  'pending_approval',
  'waiting_manual',
  'skipped',
  'failed',
]);
const RISK: ReadonlySet<string> = new Set(['R0', 'R1', 'R2', 'R3']);

type Json = Record<string, unknown>;
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);

function errorText(v: unknown): string | null {
  if (typeof v === 'string') return v;
  if (typeof v === 'object' && v !== null && 'message' in v) return str((v as Json).message);
  return null;
}

export function buildTimeline(req: ServiceRequest): TimelineStep[] {
  const plan = (req.plan as Json[] | undefined) ?? [];
  const results = new Map<number, Json>();
  for (const r of (req.step_results as Json[] | undefined) ?? []) {
    if (typeof r.index === 'number') results.set(r.index, r);
  }
  const actions = new Map((req.actions ?? []).map((a) => [a.id, a]));
  const open =
    req.state === 'submitted' || req.state === 'awaiting_approval' || req.state === 'fulfilling';

  return plan.map((step, index) => {
    const r = results.get(index);
    const manual = step.manual === true;
    const status = str(r?.status);
    let state: StepState;
    if (status && KNOWN.has(status)) state = status as StepState;
    else state = open ? 'queued' : 'not_run';
    const actionRequestId = str(r?.action_request_id);
    const risk = str(r?.risk_class);
    return {
      index,
      title: str(r?.title) ?? str(step.title) ?? `Adım ${String(index + 1)}`,
      tool: manual ? null : (str(r?.tool) ?? str(step.tool)),
      manual,
      state,
      actor: str(r?.actor),
      at: str(r?.at),
      riskClass: risk && RISK.has(risk) ? (risk as RiskClass) : null,
      action: actionRequestId ? (actions.get(actionRequestId) ?? null) : null,
      actionRequestId,
      error: errorText(r?.error),
      note: str(r?.note),
    };
  });
}
