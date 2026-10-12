import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Button } from './Button';
import styles from './Toast.module.css';
import { ToastContext, type ToastApi, type ToastOptions, type ToastTone } from './toastContext';

interface ToastItem {
  id: number;
  title: string;
  description?: string | undefined;
  tone: ToastTone;
  duration: number;
}

function ToastView({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  useEffect(() => {
    if (item.duration <= 0) return;
    const t = window.setTimeout(() => {
      onDismiss(item.id);
    }, item.duration);
    return () => {
      window.clearTimeout(t);
    };
  }, [item.id, item.duration, onDismiss]);

  return (
    <div
      className={cx(styles.toast, styles[item.tone])}
      role={item.tone === 'error' ? 'alert' : 'status'}
    >
      <p className={styles.title}>{item.title}</p>
      {item.description && <p className={styles.description}>{item.description}</p>}
      <Button
        variant="ghost"
        size="sm"
        className={styles.close}
        onClick={() => {
          onDismiss(item.id);
        }}
        aria-label="Bildirimi kapat"
      >
        ✕
      </Button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((list) => list.filter((t) => t.id !== id));
  }, []);

  const show = useCallback((options: ToastOptions) => {
    const id = nextId.current++;
    const tone = options.tone ?? 'info';
    const duration = options.duration ?? (tone === 'error' ? 8000 : 5000);
    // En fazla 4 bildirim: eskiler düşer
    setItems((list) => [
      ...list.slice(-3),
      { id, title: options.title, description: options.description, tone, duration },
    ]);
    return id;
  }, []);

  const api = useMemo<ToastApi>(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <section className={styles.region} aria-label="Bildirimler">
        {items.map((item) => (
          <ToastView key={item.id} item={item} onDismiss={dismiss} />
        ))}
      </section>
    </ToastContext.Provider>
  );
}
