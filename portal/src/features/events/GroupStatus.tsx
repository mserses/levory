import { Link } from 'react-router-dom';
import type { EventGroup } from '@/api/types';
import { Badge } from '@/components';
import { formatDateTime } from '@/lib/format';
import { EVENT_GROUP_STATE_LABELS, EVENT_SEVERITY_LABELS } from '@/lib/labels';
import { SEVERITY_TONES, isSilenced } from './model';
import styles from './Events.module.css';

export function SeverityBadge({ severity }: { severity: EventGroup['severity'] }) {
  return (
    <Badge tone={SEVERITY_TONES[severity]} dot>
      {EVENT_SEVERITY_LABELS[severity]}
    </Badge>
  );
}

/** Grup durumu: açık/çözüldü/birleşti + üstlenme, susturma, çırpınma */
export function GroupStatus({ group }: { group: EventGroup }) {
  return (
    <div className={styles.status}>
      <Badge tone={group.state === 'open' ? 'warning' : 'neutral'}>
        {EVENT_GROUP_STATE_LABELS[group.state]}
      </Badge>
      {group.acknowledged_by && (
        <span className={styles.meta}>Üstlenen: {group.acknowledged_by}</span>
      )}
      {isSilenced(group) && (
        <Badge tone="accent">Susturuldu · {formatDateTime(group.silenced_until)} kadar</Badge>
      )}
      {group.flapping && <Badge tone="info">Çırpınıyor</Badge>}
    </div>
  );
}

export function LinkedIncident({ group }: { group: EventGroup }) {
  return group.incident_id ? (
    <Link to={`/kayitlar/${encodeURIComponent(group.incident_id)}`}>Olay kaydı</Link>
  ) : (
    <span className="muted">Olay açılmadı</span>
  );
}
