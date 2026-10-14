import type { ReactNode } from 'react';
import { errorMessage, isApiError } from '@/api/errors';
import { cx } from '@/lib/cx';
import { Button } from './Button';
import styles from './State.module.css';

export interface ErrorStateProps {
  /** Yakalanan hata (ApiError ise Türkçe detail gösterilir) */
  error?: unknown;
  title?: string;
  /** error yerine doğrudan mesaj */
  message?: string;
  onRetry?: () => void;
  action?: ReactNode;
}

function defaultTitle(error: unknown): string {
  if (isApiError(error)) {
    if (error.isForbidden) return 'Yetkiniz yok';
    if (error.isNotFound) return 'Bulunamadı';
    if (error.isNetworkError) return 'Bağlantı sorunu';
    if (error.isConflict) return 'Kayıt değişti';
  }
  return 'Bir sorun oluştu';
}

export function ErrorState({ error, title, message, onRetry, action }: ErrorStateProps) {
  return (
    <section className={cx(styles.state, styles.error)} role="alert">
      <span className={styles.icon} aria-hidden="true">
        !
      </span>
      <h2 className={styles.title}>{title ?? defaultTitle(error)}</h2>
      <p className={styles.description}>{message ?? errorMessage(error)}</p>
      {(onRetry || action) && (
        <div className={styles.actions}>
          {onRetry && (
            <Button variant="secondary" onClick={onRetry}>
              Yeniden dene
            </Button>
          )}
          {action}
        </div>
      )}
    </section>
  );
}
