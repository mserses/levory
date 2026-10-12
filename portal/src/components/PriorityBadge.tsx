import type { Priority } from '@/api/types';
import { PRIORITY_LABELS } from '@/lib/labels';
import { Badge, type Tone } from './Badge';

const TONES: Record<Priority, Tone> = {
  P1: 'danger',
  P2: 'warning',
  P3: 'info',
  P4: 'neutral',
  P5: 'neutral',
};

export interface PriorityBadgeProps {
  priority: Priority;
  /** Yalnızca "P1" yaz (tam ad title/ekran okuyucuda) */
  compact?: boolean;
}

export function PriorityBadge({ priority, compact = false }: PriorityBadgeProps) {
  const full = PRIORITY_LABELS[priority];
  return (
    <Badge tone={TONES[priority]} strong={priority === 'P1' || priority === 'P2'} title={full}>
      {compact ? (
        <>
          <span aria-hidden="true">{priority}</span>
          <span className="visually-hidden">Öncelik {full}</span>
        </>
      ) : (
        full
      )}
    </Badge>
  );
}
