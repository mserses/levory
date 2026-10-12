import type { Incident, IncidentStatus } from '@/api';
import { PriorityBadge, SlaBadge, StatusBadge } from '@/components';
import { formatDateTime, formatRelative } from '@/lib/format';
import { groupLabel } from '@/lib/taxonomy';
import { serviceLabel } from '../services';
import styles from '../MyRequestsPage.module.css';

/** Durumun son kullanıcıya sade açıklaması */
const STATUS_HELP: Record<IncidentStatus, string> = {
  new: 'Talebiniz alındı; ön değerlendirmeden sonra bir destek uzmanına iletilecek.',
  assigned: 'Talebiniz bir ekibe/uzmana atandı.',
  in_progress: 'Bir destek uzmanı talebiniz üzerinde çalışıyor.',
  on_hold: 'Talebiniz beklemede.',
  resolved: 'Talebiniz çözüldü olarak işaretlendi. Sorun devam ediyorsa yeni bir talep açın.',
  closed: 'Talebiniz kapatıldı.',
  cancelled: 'Talebiniz iptal edildi.',
};

export interface RequestDetailsProps {
  incident: Incident;
}

/**
 * Self-servis kayıt ayrıntısı. Bilinçli olarak yapay zekâ iç ayrıntısı içermez
 * (güven skoru, kararlar, kategori önerisi yok); yalnızca kullanıcının işine yarayan bilgiler.
 */
export function RequestDetails({ incident }: RequestDetailsProps) {
  const waitingForMe =
    incident.status === 'on_hold' && incident.on_hold_reason === 'awaiting_caller';
  const open = !['resolved', 'closed', 'cancelled'].includes(incident.status);

  return (
    <div className="stack">
      {waitingForMe && (
        <p className={styles.callout} role="note">
          <strong>Sizden yanıt bekleniyor.</strong> Destek ekibi ek bilgiye ihtiyaç duyuyor; size
          ulaşan e-posta veya mesajı yanıtlayın. Bu sürede hedef süre işlemez.
        </p>
      )}

      <dl className={styles.details}>
        <div>
          <dt>Durum</dt>
          <dd>
            <StatusBadge status={incident.status} onHoldReason={incident.on_hold_reason} />
            <span className={styles.help}>{STATUS_HELP[incident.status]}</span>
          </dd>
        </div>
        <div>
          <dt>Öncelik</dt>
          <dd>
            <PriorityBadge priority={incident.priority} />
          </dd>
        </div>
        <div>
          <dt>Hedef süre</dt>
          <dd>
            <SlaBadge incident={incident} />
            {open && incident.response_due_at && !incident.responded_at && (
              <span className={styles.help}>
                İlk yanıt hedefi: {formatDateTime(incident.response_due_at)}
              </span>
            )}
            {incident.resolve_due_at && (
              <span className={styles.help}>
                Çözüm hedefi: {formatDateTime(incident.resolve_due_at)}
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt>Hizmet</dt>
          <dd>{serviceLabel(incident.service)}</dd>
        </div>
        <div>
          <dt>İlgilenen ekip</dt>
          <dd>
            {incident.assignment_group ? groupLabel(incident.assignment_group) : 'Henüz atanmadı'}
          </dd>
        </div>
        <div>
          <dt>Oluşturulma</dt>
          <dd>
            {formatDateTime(incident.created_at)}{' '}
            <span className="muted">({formatRelative(incident.created_at)})</span>
          </dd>
        </div>
        <div>
          <dt>Son güncelleme</dt>
          <dd>{formatDateTime(incident.updated_at)}</dd>
        </div>
        {incident.resolved_at && (
          <div>
            <dt>Çözülme</dt>
            <dd>{formatDateTime(incident.resolved_at)}</dd>
          </div>
        )}
      </dl>

      <section aria-labelledby={`aciklama-${incident.id}`}>
        <h3 id={`aciklama-${incident.id}`} className={styles.sectionTitle}>
          Açıklamanız
        </h3>
        {incident.description ? (
          <p className="prewrap">{incident.description}</p>
        ) : (
          <p className="muted">Açıklama girilmemiş.</p>
        )}
      </section>

      {incident.resolution && (
        <section aria-labelledby={`cozum-${incident.id}`}>
          <h3 id={`cozum-${incident.id}`} className={styles.sectionTitle}>
            Çözüm notu
          </h3>
          <p className="prewrap">{incident.resolution}</p>
        </section>
      )}
    </div>
  );
}
