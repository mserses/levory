import { useContext } from 'react';
import { AuthContext, DEFAULT_AI_CONFIDENCE_THRESHOLD } from '@/auth/AuthContext';
import { cx } from '@/lib/cx';
import { formatPercent } from '@/lib/format';
import styles from './ConfidenceMeter.module.css';

export interface ConfidenceMeterProps {
  /** 0–1 arası güven skoru */
  value: number;
  /** Politika eşiği; verilmezse oturumdaki /me değeri (backend SZ_AI_CONFIDENCE_THRESHOLD) */
  threshold?: number;
  label?: string;
}

/** Yapay zekâ güven skoru; eşik çizgisi ve eşik altı uyarısıyla. */
export function ConfidenceMeter({
  value,
  threshold: thresholdProp,
  label = 'Güven',
}: ConfidenceMeterProps) {
  const auth = useContext(AuthContext);
  const threshold = thresholdProp ?? auth?.aiConfidenceThreshold ?? DEFAULT_AI_CONFIDENCE_THRESHOLD;
  const v = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  const level = v >= threshold ? 'high' : v >= 0.5 ? 'mid' : 'low';
  const text = formatPercent(v);
  return (
    <div className={cx(styles.meter, styles[level])}>
      <span className={styles.label} aria-hidden="true">
        {label}
      </span>
      <div
        className={styles.track}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(v * 100)}
        aria-valuetext={`${text}${v < threshold ? ', eşiğin altında' : ''}`}
      >
        <span className={styles.fill} style={{ width: `${v * 100}%` }} />
        <span
          className={styles.threshold}
          style={{ left: `${threshold * 100}%` }}
          aria-hidden="true"
        />
      </div>
      <span className={styles.value} aria-hidden="true">
        {text}
      </span>
      {v < threshold && (
        <span className={styles.note} aria-hidden="true">
          Eşiğin ({formatPercent(threshold)}) altında — insan incelemesi gerekir
        </span>
      )}
    </div>
  );
}
