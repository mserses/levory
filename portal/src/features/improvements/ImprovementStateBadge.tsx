import type { ImprovementState } from '@/api/types';
import { Badge, type Tone } from '@/components';
import { IMPROVEMENT_STATE_LABELS } from '@/lib/labels';

const TONES: Record<ImprovementState, Tone> = {
  idea: 'info',
  approved: 'accent',
  in_progress: 'warning',
  measured: 'warning',
  closed: 'success',
  rejected: 'neutral',
};

export function ImprovementStateBadge({ state }: { state: ImprovementState }) {
  return <Badge tone={TONES[state]}>{IMPROVEMENT_STATE_LABELS[state]}</Badge>;
}
