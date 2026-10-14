import { formatDateTime, formatDuration } from '@/lib/format';
import { computeSla, type SlaFields, type SlaMetric, type SlaState } from '@/lib/sla';
import { Badge, type Tone } from './Badge';

const TONES: Record<SlaState, Tone> = {
  none: 'neutral',
  on_track: 'success',
  at_risk: 'warning',
  breached: 'danger',
  paused: 'info',
  met: 'success',
  missed: 'danger',
};

const METRIC: Record<SlaMetric, string> = { response: 'Yanıt', resolve: 'Çözüm' };

export interface SlaBadgeProps {
  incident: SlaFields;
  /** Testler ve canlı sayaçlar için "şimdi" */
  now?: Date;
}

export function SlaBadge({ incident, now }: SlaBadgeProps) {
  const sla = computeSla(incident, now);
  const metric = sla.metric ? METRIC[sla.metric] : '';
  let text: string;
  switch (sla.state) {
    case 'none':
      text = 'SLA yok';
      break;
    case 'paused':
      text = 'SLA duraklatıldı';
      break;
    case 'met':
      text = 'SLA karşılandı';
      break;
    case 'missed':
      text = 'SLA kaçırıldı';
      break;
    case 'breached':
      text = `${metric} SLA aşıldı · ${formatDuration(sla.remainingSeconds ?? 0)}`;
      break;
    case 'at_risk':
    case 'on_track':
      text = `${metric}: ${formatDuration(sla.remainingSeconds ?? 0)} kaldı`;
      break;
  }
  const title = sla.dueAt ? `${metric} vadesi: ${formatDateTime(sla.dueAt)}` : undefined;
  return (
    <Badge
      tone={TONES[sla.state]}
      dot
      strong={sla.state === 'breached'}
      title={title}
      data-sla-state={sla.state}
    >
      {text}
    </Badge>
  );
}
