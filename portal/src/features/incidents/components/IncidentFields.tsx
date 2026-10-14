import type { Incident } from '@/api/types';
import { ConfidenceMeter, PriorityBadge, SlaBadge, StatusBadge } from '@/components';
import { formatDateTime, formatDuration } from '@/lib/format';
import { IMPACT_LABELS, KIND_LABELS, ON_HOLD_REASON_LABELS, URGENCY_LABELS } from '@/lib/labels';
import { taxonomyLabel, type TaxonomyField } from '@/lib/taxonomy';
import { pausesSla } from '../rules';
import styles from './IncidentFields.module.css';

const dash = <span className="muted">—</span>;
const text = (v: string | null | undefined) => (v ? v : dash);
const taxonomyText = (field: TaxonomyField, v: string | null | undefined) =>
  v ? taxonomyLabel(field, v) : dash;

/** Kaydın tüm alanları (salt okunur) */
export function IncidentFields({ incident, now }: { incident: Incident; now: Date }) {
  return (
    <div className="stack">
      <div>
        <h3 className={styles.subTitle}>Açıklama</h3>
        <p className="prewrap" style={{ margin: 0 }}>
          {incident.description || <span className="muted">Açıklama yok.</span>}
        </p>
      </div>
      <dl className={styles.grid}>
        <div>
          <dt>Tür</dt>
          <dd>{KIND_LABELS[incident.kind]}</dd>
        </div>
        <div>
          <dt>Arayan</dt>
          <dd>{incident.caller}</dd>
        </div>
        <div>
          <dt>Öncelik</dt>
          <dd>
            <PriorityBadge priority={incident.priority} />
          </dd>
        </div>
        <div>
          <dt>Durum</dt>
          <dd>
            <StatusBadge status={incident.status} onHoldReason={incident.on_hold_reason} />
          </dd>
        </div>
        <div>
          <dt>Etki</dt>
          <dd>{IMPACT_LABELS[incident.impact]}</dd>
        </div>
        <div>
          <dt>Aciliyet</dt>
          <dd>{URGENCY_LABELS[incident.urgency]}</dd>
        </div>
        <div>
          <dt>Kategori</dt>
          <dd>{taxonomyText('category', incident.category)}</dd>
        </div>
        <div>
          <dt>Hizmet</dt>
          <dd>{taxonomyText('service', incident.service)}</dd>
        </div>
        <div>
          <dt>Atama grubu</dt>
          <dd>{taxonomyText('assignment_group', incident.assignment_group)}</dd>
        </div>
        <div>
          <dt>Atanan</dt>
          <dd>{text(incident.assignee)}</dd>
        </div>
        {incident.status === 'on_hold' && (
          <div>
            <dt>Bekleme nedeni</dt>
            <dd>
              {incident.on_hold_reason ? ON_HOLD_REASON_LABELS[incident.on_hold_reason] : dash}
            </dd>
          </div>
        )}
        <div className={styles.wide}>
          <dt>Çözüm</dt>
          <dd className="prewrap">{text(incident.resolution)}</dd>
        </div>
      </dl>

      <div>
        <h3 className={styles.subTitle}>SLA</h3>
        <div className="row" style={{ marginBottom: 'var(--space-2)' }}>
          <SlaBadge incident={incident} now={now} />
          {incident.sla_paused_at && (
            <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
              {formatDateTime(incident.sla_paused_at)} itibarıyla duraklatıldı
              {pausesSla(incident.on_hold_reason) ? ' (arayan/tedarikçi beklemesi)' : ''}
            </span>
          )}
        </div>
        <dl className={styles.grid}>
          <div>
            <dt>Yanıt vadesi</dt>
            <dd>{formatDateTime(incident.response_due_at)}</dd>
          </div>
          <div>
            <dt>İlk yanıt</dt>
            <dd>{formatDateTime(incident.responded_at)}</dd>
          </div>
          <div>
            <dt>Çözüm vadesi</dt>
            <dd>{formatDateTime(incident.resolve_due_at)}</dd>
          </div>
          <div>
            <dt>Çözüldü</dt>
            <dd>{formatDateTime(incident.resolved_at)}</dd>
          </div>
          <div>
            <dt>Toplam duraklatma</dt>
            <dd>
              {incident.sla_paused_seconds > 0 ? formatDuration(incident.sla_paused_seconds) : dash}
            </dd>
          </div>
        </dl>
      </div>

      <dl className={styles.grid}>
        <div>
          <dt>Oluşturulma</dt>
          <dd>{formatDateTime(incident.created_at)}</dd>
        </div>
        <div>
          <dt>Son güncelleme</dt>
          <dd>{formatDateTime(incident.updated_at)}</dd>
        </div>
        <div>
          <dt>Sürüm</dt>
          <dd>{incident.version}</dd>
        </div>
        <div className={styles.wide}>
          <dt>Yapay zekâ güveni (son sınıflandırma)</dt>
          <dd>
            {incident.ai_confidence === null ? (
              <span className="muted">Henüz yapay zekâ ile sınıflandırılmadı.</span>
            ) : (
              <ConfidenceMeter value={incident.ai_confidence} label="Yapay zekâ güveni" />
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}
