import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from '../Approvals.module.css';

export interface TabItem<K extends string> {
  key: K;
  label: string;
  hint?: string;
}

interface TabsProps<K extends string> {
  label: string;
  items: readonly TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  children: ReactNode;
}

/**
 * WAI-ARIA sekme deseni: ok tuşları/Home/End ile gezinme, otomatik etkinleştirme,
 * yalnızca etkin sekme Tab sırasında.
 */
export function Tabs<K extends string>({ label, items, active, onChange, children }: TabsProps<K>) {
  const base = useId();
  const refs = useRef(new Map<K, HTMLButtonElement>());
  const tabId = (k: K) => `${base}-tab-${k}`;
  const panelId = `${base}-panel`;

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = items.findIndex((i) => i.key === active);
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % items.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length - 1;
    else return;
    event.preventDefault();
    const target = items[next];
    if (!target) return;
    onChange(target.key);
    refs.current.get(target.key)?.focus();
  };

  return (
    <div className={styles.tabsRoot}>
      <div role="tablist" aria-label={label} className={styles.tablist}>
        {items.map((item) => {
          const selected = item.key === active;
          return (
            <button
              key={item.key}
              ref={(el) => {
                if (el) refs.current.set(item.key, el);
                else refs.current.delete(item.key);
              }}
              type="button"
              role="tab"
              id={tabId(item.key)}
              aria-selected={selected}
              aria-controls={panelId}
              tabIndex={selected ? 0 : -1}
              title={item.hint}
              className={cx(styles.tab, selected && styles.tabActive)}
              onClick={() => {
                onChange(item.key);
              }}
              onKeyDown={onKeyDown}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={panelId} aria-labelledby={tabId(active)} className={styles.panel}>
        {children}
      </div>
    </div>
  );
}
