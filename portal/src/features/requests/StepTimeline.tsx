import { Link } from 'react-router-dom';
import type { ServiceRequest } from '@/api/types';
import { Badge, type Tone } from '@/components';
import { formatDateTime } from '@/lib/format';
import { ACTION_STATE_LABELS, RISK_CLASS_LABELS, actorLabel } from '@/lib/labels';
import { buildTimeline, STEP_STATE_LABELS, type StepState, type TimelineStep } from './steps';
import styles from './Requests.module.css';

const TONES: Record<StepState, Tone> = {
  executed: 'success',
  pending_approval: 'warning',
  waiting_manual: 'warning',
  skipped: 'neutral',
  failed: 'danger',
  queued: 'neutral',
  not_run: 'neutral',
};

const MARKERS: Record<StepState, string> = {
  executed: '✓',
  pending_approval: '…',
  waiting_manual: '…',
  skipped: '–',
  failed: '!',
  queued: '',
  not_run: '–',
};

interface Props {
  request: ServiceRequest;
  /** Eylem isteğini /onaylar'da açabilir mi (approver / change_manager) */
  canOpenApprovals: boolean;
}

/** Karşılama planı adımları: araç, durum, kim/ne çalıştırdı, bekleyen onay bağlantısı */
export function StepTimeline({ request, canOpenApprovals }: Props) {
  const steps = buildTimeline(request);
  if (steps.length === 0) {
    return <p className="muted">Bu talebin karşılama planında adım yok.</p>;
  }
  return (
    <ol className={styles.timeline} aria-label="Karşılama adımları">
      {steps.map((step) => (
        <Step key={step.index} step={step} canOpenApprovals={canOpenApprovals} />
      ))}
    </ol>
  );
}

function Step({ step, canOpenApprovals }: { step: TimelineStep; canOpenApprovals: boolean }) {
  const label = STEP_STATE_LABELS[step.state];
  return (
    <li className={styles.step} data-step-state={step.state}>
      <span className={styles.marker} data-state={step.state} aria-hidden="true">
        {MARKERS[step.state] || String(step.index + 1)}
      </span>
      <div className={styles.stepBody}>
        <h3 className={styles.stepTitle}>
          <span className="visually-hidden">Adım {step.index + 1}: </span>
          {step.title}
        </h3>
        <div className="row" style={{ gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          <Badge tone={TONES[step.state]} dot>
            {label}
          </Badge>
          {step.riskClass && <Badge tone="neutral">{RISK_CLASS_LABELS[step.riskClass]}</Badge>}
        </div>
        <p className={styles.stepMeta}>
          {step.manual ? (
            'El ile adım (insan işi)'
          ) : (
            <>
              Araç: <code className={styles.tool}>{step.tool ?? '—'}</code>
            </>
          )}
        </p>
        {step.actor && (
          <p className={styles.stepMeta}>
            Çalıştıran: {actorLabel(step.actor)}
            {step.at && ` · ${formatDateTime(step.at)}`}
          </p>
        )}
        {!step.actor && step.at && step.state !== 'queued' && (
          <p className={styles.stepMeta}>{formatDateTime(step.at)}</p>
        )}
        {step.note && <p className={`${styles.stepMeta} prewrap`}>Not: {step.note}</p>}
        {step.error && <p className={styles.stepError}>{step.error}</p>}
        {step.actionRequestId && <ActionLink step={step} canOpenApprovals={canOpenApprovals} />}
      </div>
    </li>
  );
}

function ActionLink({ step, canOpenApprovals }: { step: TimelineStep; canOpenApprovals: boolean }) {
  const action = step.action;
  const number = action?.number ?? 'Eylem isteği';
  const id = step.actionRequestId ?? '';
  const pending = action ? action.state === 'pending' : step.state === 'pending_approval';
  const status = action ? ACTION_STATE_LABELS[action.state] : null;
  const detail =
    pending && action
      ? `${String(action.required_approvals)} onay gerekir · son tarih ${formatDateTime(action.expires_at)}`
      : status;
  if (canOpenApprovals) {
    return (
      <p className={styles.stepMeta}>
        <Link to={`/onaylar?sekme=eylem&istek=${encodeURIComponent(id)}`}>
          {number} {pending ? '— onay kutusunda aç' : '— eylem isteğini aç'}
        </Link>
        {detail && ` (${detail})`}
      </p>
    );
  }
  return (
    <p className={styles.stepMeta}>
      {pending
        ? `${number}: BT onayı bekleniyor${detail ? ` (${detail})` : ''}. Onaylanınca karşılama kendiliğinden sürer.`
        : `${number}${status ? `: ${status}` : ''}`}
    </p>
  );
}
