import type { ProblemState } from '@/api/types';
import { Badge, type Tone } from '@/components';
import { PROBLEM_STATE_LABELS } from '@/lib/labels';

const TONES: Record<ProblemState, Tone> = {
  new: 'info',
  investigating: 'warning',
  known_error: 'accent',
  resolved: 'success',
  closed: 'neutral',
};

export function ProblemStateBadge({ state }: { state: ProblemState }) {
  return (
    <Badge tone={TONES[state]} dot>
      {PROBLEM_STATE_LABELS[state]}
    </Badge>
  );
}
