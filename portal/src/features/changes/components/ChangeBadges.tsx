import type { ChangeState, ChangeType, RiskLevel } from '@/api/types';
import { Badge, type Tone } from '@/components';
import { CHANGE_STATE_LABELS, CHANGE_TYPE_LABELS, RISK_LEVEL_LABELS } from '@/lib/labels';

const STATE_TONES: Record<ChangeState, Tone> = {
  draft: 'neutral',
  assess: 'info',
  authorize: 'warning',
  scheduled: 'accent',
  implement: 'info',
  review: 'info',
  closed: 'success',
  rejected: 'danger',
  cancelled: 'neutral',
  failed: 'danger',
};

const TYPE_TONES: Record<ChangeType, Tone> = {
  standard: 'success',
  normal: 'neutral',
  emergency: 'danger',
};

const RISK_TONES: Record<RiskLevel, Tone> = {
  low: 'success',
  medium: 'info',
  high: 'warning',
  critical: 'danger',
};

export function ChangeStateBadge({ state }: { state: ChangeState }) {
  return (
    <Badge tone={STATE_TONES[state]} dot>
      {CHANGE_STATE_LABELS[state]}
    </Badge>
  );
}

export function ChangeTypeBadge({ type }: { type: ChangeType }) {
  return (
    <Badge tone={TYPE_TONES[type]} strong={type === 'emergency'}>
      {CHANGE_TYPE_LABELS[type]}
    </Badge>
  );
}

/** Risk seviyesi + skor; seviye yoksa "Değerlendirilmedi" */
export function RiskBadge({ level, score }: { level: RiskLevel | null; score?: number | null }) {
  if (!level) return <Badge tone="neutral">Risk değerlendirilmedi</Badge>;
  return (
    <Badge tone={RISK_TONES[level]} dot strong={level === 'critical'}>
      Risk: {RISK_LEVEL_LABELS[level]}
      {score !== null && score !== undefined ? ` (${score})` : ''}
    </Badge>
  );
}
