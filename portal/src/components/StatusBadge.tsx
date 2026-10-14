import type { IncidentStatus, OnHoldReason } from '@/api/types';
import { ON_HOLD_REASON_LABELS, STATUS_LABELS } from '@/lib/labels';
import { Badge, type Tone } from './Badge';

const TONES: Record<IncidentStatus, Tone> = {
  new: 'info',
  assigned: 'info',
  in_progress: 'accent',
  on_hold: 'warning',
  resolved: 'success',
  closed: 'neutral',
  cancelled: 'neutral',
};

export interface StatusBadgeProps {
  status: IncidentStatus;
  /** on_hold ise bekleme nedeni (title ve ekran okuyucu metninde gösterilir) */
  onHoldReason?: OnHoldReason | null | undefined;
}

export function StatusBadge({ status, onHoldReason }: StatusBadgeProps) {
  const reason = status === 'on_hold' && onHoldReason ? ON_HOLD_REASON_LABELS[onHoldReason] : null;
  return (
    <Badge tone={TONES[status]} dot title={reason ?? undefined}>
      {STATUS_LABELS[status]}
      {reason && <span className="visually-hidden">: {reason}</span>}
    </Badge>
  );
}
