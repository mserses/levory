import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '@/components';
import { FOCUSABLE, restoreFocus, trapTabKey } from '@/components/focusTrap';
import styles from './Drawer.module.css';

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Başlığın üstündeki küçük metin (ör. kayıt numarası) */
  eyebrow?: string;
  children: ReactNode;
}

/**
 * Sağdan açılan yan panel (mobilde tam ekran). Dialog ile aynı erişilebilirlik sözleşmesi:
 * odak tuzağı, Escape ile kapanma, kapanınca odağı tetikleyen öğeye geri verme.
 * Şimdilik yalnızca self-servis kullanıyor; ikinci kullanıcı çıkınca components/'e taşınabilir.
 */
export function Drawer({ open, onClose, title, eyebrow, children }: DrawerProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = ref.current;
    (node?.querySelector<HTMLElement>(FOCUSABLE) ?? node)?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (!node) return;
      if (event.key === 'Escape') {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      trapTabKey(event, node);
    };
    document.addEventListener('keydown', onKeyDown);
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = overflow;
      restoreFocus(previous);
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className={styles.overlay}
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className={styles.header}>
          <div className={styles.headings}>
            {eyebrow && <p className={styles.eyebrow}>{eyebrow}</p>}
            <h2 className={styles.title} id={titleId}>
              {title}
            </h2>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Ayrıntıları kapat">
            ✕
          </Button>
        </header>
        <div className={styles.body}>{children}</div>
      </div>
    </div>,
    document.body,
  );
}
