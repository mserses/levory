/**
 * change_risk AIDecision çıktısını (agents/change_risk.py) ekranda gösterilebilir hâle getirir.
 * Çıktı serbest JSON olduğu için her alan savunmacı okunur.
 */
import type { AIDecision, RiskLevel } from '@/api/types';

const FACTOR_LABELS: Record<string, string> = {
  change_type: 'Değişiklik türü',
  criticality: 'En yüksek CI kritikliği',
  production: 'Üretim ortamı',
  business_services: 'Etkilenen iş hizmetleri',
  failure_history: 'Geçmiş başarısız değişiklikler',
  recent_incidents: 'Son 30 gün olayları',
  backout_plan: 'Geri dönüş planı',
  blast_radius: 'Etki alanı',
  test_plan: 'Test planı',
};

const RISK_LEVELS: readonly RiskLevel[] = ['low', 'medium', 'high', 'critical'];

export interface RiskFactor {
  key: string;
  label: string;
  points: number;
}

export interface RiskInsight {
  deterministicScore: number | null;
  deterministicLevel: RiskLevel | null;
  factors: RiskFactor[];
  notes: string[];
  narrative: string;
  checklist: string[];
  adjustment: number;
  justification: string;
  proposedScore: number | null;
  proposedLevel: RiskLevel | null;
  currentScore: number | null;
  /** Öneri mevcut skoru düşürüyor (her zaman insan onayı ister) */
  lowersRisk: boolean;
  applied: boolean;
  policyReason: string;
  llmError: string;
}

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Obj) : {};
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.trim() !== '') : [];
const level = (v: unknown): RiskLevel | null =>
  typeof v === 'string' && (RISK_LEVELS as readonly string[]).includes(v) ? (v as RiskLevel) : null;

export function riskInsight(decision: Pick<AIDecision, 'output' | 'rationale'>): RiskInsight {
  const output = obj(decision.output);
  const det = obj(output.deterministic);
  const llm = obj(output.llm);
  const proposed = obj(output.proposed);
  const factors = Object.entries(obj(det.factors))
    .map(([key, value]) => ({
      key,
      label: FACTOR_LABELS[key] ?? 'Diğer bileşen',
      points: num(value),
    }))
    .filter((f): f is RiskFactor => f.points !== null);
  const proposedScore = num(proposed.risk_score);
  const currentScore = num(output.current_risk_score);
  return {
    deterministicScore: num(det.score),
    deterministicLevel: level(det.level),
    factors,
    notes: strList(det.notes),
    narrative: str(llm.narrative) || decision.rationale,
    checklist: strList(llm.checklist),
    adjustment: num(llm.adjustment) ?? 0,
    justification: str(llm.justification),
    proposedScore,
    proposedLevel: level(proposed.risk_level),
    currentScore,
    lowersRisk: proposedScore !== null && currentScore !== null && proposedScore < currentScore,
    applied: output.applied === true,
    policyReason: str(output.policy_reason),
    llmError: str(llm.error),
  };
}

/** Değişikliğin en yeni yapay zekâ risk kararı */
export function latestRiskDecision(decisions: readonly AIDecision[]): AIDecision | null {
  const risk = decisions.filter(
    (d) => d.action === 'assess_risk' || d.agent === 'change_risk' || d.agent === 'change',
  );
  return risk.reduce<AIDecision | null>(
    (latest, d) => (latest === null || d.created_at > latest.created_at ? d : latest),
    null,
  );
}
