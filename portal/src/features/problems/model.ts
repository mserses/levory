/**
 * Sorun yönetimi yardımcıları (backend `services/problems.py`, `docs/faz3.md`):
 * durum makinesi, RCA düzenleyici modeli, yapay zekâ uç sonuçlarının yorumu.
 */
import type { AgentResult, ProblemState, RCA, RCAMethod } from '@/api/types';

/** `services.problems.transition` izinli geçişleri */
export const PROBLEM_TRANSITIONS: Record<ProblemState, readonly ProblemState[]> = {
  new: ['investigating', 'closed'],
  investigating: ['known_error', 'resolved', 'closed'],
  known_error: ['investigating', 'resolved', 'closed'],
  resolved: ['investigating', 'closed'],
  closed: [],
};

export const TREND_KIND_LABELS: Record<string, string> = {
  ci_category: 'Aynı CI ve kategori',
  similar_title: 'Benzer başlık',
};

export const MAX_RCA_STEPS = 10;

export interface RcaStepDraft {
  key: number;
  question: string;
  answer: string;
  /** datetime-local değeri (yerel saat) */
  at: string;
}

export interface RcaDraft {
  method: RCAMethod;
  steps: RcaStepDraft[];
  rootCause: string;
}

let stepKey = 0;
export const newStep = (partial: Partial<Omit<RcaStepDraft, 'key'>> = {}): RcaStepDraft => ({
  key: ++stepKey,
  question: '',
  answer: '',
  at: '',
  ...partial,
});

type Json = Record<string, unknown>;
const str = (v: unknown) => (typeof v === 'string' ? v : '');

/** ISO → `YYYY-MM-DDTHH:mm` (tarayıcı yerel saati) */
export function toLocalInput(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${String(d.getFullYear())}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Sunucudaki RCA (`rca` ya da `rca_draft`) → düzenleyici */
export function rcaToDraft(rca: Json | null | undefined): RcaDraft {
  const method: RCAMethod = rca?.method === 'timeline' ? 'timeline' : '5_why';
  const steps = Array.isArray(rca?.steps)
    ? (rca.steps as unknown[])
        .filter((s): s is Json => typeof s === 'object' && s !== null)
        .map((s) =>
          newStep({
            question: str(s.question),
            answer: str(s.answer),
            at: s.at ? toLocalInput(str(s.at)) : '',
          }),
        )
    : [];
  return {
    method,
    steps: steps.length > 0 ? steps : [newStep()],
    rootCause: str(rca?.root_cause),
  };
}

/** Backend RCA şemasıyla aynı kurallar; hata yoksa boş dizi */
export function validateRca(draft: RcaDraft): string[] {
  const errors: string[] = [];
  if (draft.steps.length === 0) errors.push('En az bir adım ekleyin.');
  if (draft.steps.length > MAX_RCA_STEPS) {
    errors.push(`En fazla ${String(MAX_RCA_STEPS)} adım olabilir.`);
  }
  draft.steps.forEach((s, i) => {
    const n = String(i + 1);
    if (!s.answer.trim()) errors.push(`Adım ${n}: yanıt zorunludur.`);
    if (draft.method === '5_why' && !s.question.trim()) {
      errors.push(`Adım ${n}: 5 Neden adımında soru zorunludur.`);
    }
    if (draft.method === 'timeline' && !s.at) {
      errors.push(`Adım ${n}: zaman çizelgesi adımında zaman zorunludur.`);
    }
  });
  return errors;
}

export function draftToRca(draft: RcaDraft): RCA {
  return {
    method: draft.method,
    steps: draft.steps.map((s) => ({
      answer: s.answer.trim(),
      ...(draft.method === '5_why' ? { question: s.question.trim() } : {}),
      ...(draft.method === 'timeline' && s.at ? { at: new Date(s.at).toISOString() } : {}),
    })),
    root_cause: draft.rootCause.trim() || null,
  };
}

export type AgentOutcome =
  | { kind: 'executed'; changeId: string | null; changeNumber: string | null }
  | { kind: 'pending_approval'; actionRequestId: string | null; message: string }
  | { kind: 'error'; message: string };

/** Yapay zekâ uç sonucu (`AgentResultRead`): yürütüldü / onaya düştü / reddedildi-başarısız */
export function agentOutcome(result: AgentResult): AgentOutcome {
  if (result.status === 'executed') {
    const change = (result.result?.change ?? null) as Json | null;
    return {
      kind: 'executed',
      changeId: change ? str(change.id) || null : null,
      changeNumber: change ? str(change.number) || null : null,
    };
  }
  if (result.status === 'pending_approval') {
    return {
      kind: 'pending_approval',
      actionRequestId: result.action_request_id ?? null,
      message: result.message || 'İstek insan onayına gönderildi.',
    };
  }
  const err = result.error as Json | null | undefined;
  return {
    kind: 'error',
    message: str(err?.message) || result.message || 'Yapay zekâ isteği tamamlanamadı.',
  };
}
