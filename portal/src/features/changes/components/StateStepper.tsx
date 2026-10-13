import type { Change } from '@/api/types';
import { CHANGE_STATE_LABELS } from '@/lib/labels';
import styles from '../Changes.module.css';
import { stepsFor } from '../rules';

const STATUS_TEXT = { done: 'tamamlandı', current: 'şu anki aşama', upcoming: 'sırada' } as const;

/** Aşama göstergesi (ITIL değişiklik yaşam döngüsü); uç durumlar ayrıca belirtilir */
export function StateStepper({ change }: { change: Change }) {
  const steps = stepsFor(change);
  const ended = change.state === 'rejected' || change.state === 'cancelled';
  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }}>
      <ol className={styles.stepper} aria-label="Değişiklik aşamaları">
        {steps.map((step, i) => (
          <li
            key={step.state}
            className={styles.step}
            data-status={step.status}
            data-failed={step.state === 'failed'}
            aria-current={step.status === 'current' ? 'step' : undefined}
          >
            <span className={styles.stepIndex} aria-hidden="true">
              {step.status === 'done' ? '✓' : i + 1}
            </span>
            <span className={styles.stepLabel}>{step.label}</span>
            <span className="visually-hidden"> ({STATUS_TEXT[step.status]})</span>
          </li>
        ))}
      </ol>
      {ended && (
        <p className={styles.warningBox} style={{ margin: 0 }}>
          Bu değişiklik <strong>{CHANGE_STATE_LABELS[change.state].toLocaleLowerCase('tr')}</strong>
          ; başka geçiş yapılamaz. Yeniden planlamak için yeni bir değişiklik açın.
        </p>
      )}
    </div>
  );
}
