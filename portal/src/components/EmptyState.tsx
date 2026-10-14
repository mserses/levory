import type { ReactNode } from 'react';
import styles from './State.module.css';

export interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  /** Örn. "Yeni talep oluştur" butonu */
  action?: ReactNode;
  icon?: ReactNode;
  /** Başlık düzeyi (sayfa yapısına uygun seçin) */
  headingLevel?: 2 | 3;
}

export function EmptyState({
  title,
  description,
  action,
  icon = '∅',
  headingLevel = 2,
}: EmptyStateProps) {
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <section className={styles.state} aria-label={title}>
      <span className={styles.icon} aria-hidden="true">
        {icon}
      </span>
      <Heading className={styles.title}>{title}</Heading>
      {description && <p className={styles.description}>{description}</p>}
      {action && <div className={styles.actions}>{action}</div>}
    </section>
  );
}
