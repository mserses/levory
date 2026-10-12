import { cx } from '@/lib/cx';
import styles from './Spinner.module.css';

export interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  /** Ekran okuyucu metni; boş verilirse gösterge dekoratiftir (ör. buton içinde) */
  label?: string;
  /** Alanı ortalayan blok gösterim (sayfa/bölüm yüklenirken) */
  block?: boolean;
  /** Etiketi görünür yaz */
  showLabel?: boolean;
}

export function Spinner({
  size = 'md',
  label = 'Yükleniyor…',
  block = false,
  showLabel = false,
}: SpinnerProps) {
  const decorative = label === '';
  const content = (
    <span className={styles.wrap} {...(decorative ? { 'aria-hidden': true } : { role: 'status' })}>
      <span className={cx(styles.circle, styles[size])} aria-hidden="true" />
      {!decorative && <span className={showLabel ? undefined : 'visually-hidden'}>{label}</span>}
    </span>
  );
  return block ? <div className={styles.block}>{content}</div> : content;
}
