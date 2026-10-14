import type { DecisionOutcome } from '@/api/types';
import { OUTCOME_DESCRIPTIONS, OUTCOME_LABELS } from '@/lib/labels';
import { Badge, type Tone } from './Badge';

const TONES: Record<DecisionOutcome, Tone> = {
  auto_applied: 'success',
  suggested: 'info',
  needs_approval: 'warning',
  escalated: 'accent',
  rejected: 'danger',
};

/** Yapay zekâ kararının politika sonucu (her kararda gösterilmelidir). */
export function OutcomeBadge({ outcome }: { outcome: DecisionOutcome }) {
  return (
    <Badge
      tone={TONES[outcome]}
      dot
      strong={outcome === 'needs_approval'}
      title={OUTCOME_DESCRIPTIONS[outcome]}
    >
      {OUTCOME_LABELS[outcome]}
    </Badge>
  );
}
