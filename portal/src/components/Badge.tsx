import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Badge.module.css';

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent';

export interface BadgeProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children'> {
  tone?: Tone;
  /** Başta küçük renk noktası (renk tek başına anlam taşımaz; metin her zaman var) */
  dot?: boolean;
  strong?: boolean;
  children: ReactNode;
}

export function Badge({
  tone = 'neutral',
  dot = false,
  strong = false,
  className,
  children,
  ...rest
}: BadgeProps) {
  return (
    <span
      className={cx(
        styles.badge,
        styles[tone],
        dot && styles.dot,
        strong && styles.strong,
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
