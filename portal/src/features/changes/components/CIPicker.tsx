import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { cmdbApi, queryKeys } from '@/api';
import type { ConfigurationItem } from '@/api/types';
import { Button, Spinner, TextInput } from '@/components';
import { CI_CLASS_LABELS, CI_ENVIRONMENT_LABELS, criticalityLabel } from '@/lib/labels';
import styles from '../Changes.module.css';

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(value);
    }, ms);
    return () => {
      clearTimeout(t);
    };
  }, [value, ms]);
  return debounced;
}

export interface CIPickerProps {
  selected: ConfigurationItem[];
  onChange: (next: ConfigurationItem[]) => void;
}

const describe = (ci: ConfigurationItem) =>
  `${CI_CLASS_LABELS[ci.ci_class]}, ${CI_ENVIRONMENT_LABELS[ci.environment]}, ${criticalityLabel(ci.criticality)}`;

/** Etkilenen CI seçici: ada göre arama (GET /cmdb/cis?q=) + seçili liste */
export function CIPicker({ selected, onChange }: CIPickerProps) {
  const [term, setTerm] = useState('');
  const q = useDebounced(term.trim(), 200);
  const search = useQuery({
    queryKey: queryKeys.cmdb.list({ q, limit: 20 }),
    queryFn: ({ signal }) => cmdbApi.list({ q, limit: 20 }, signal),
    enabled: q.length >= 2,
  });
  const selectedIds = new Set(selected.map((c) => c.id));
  const results = (search.data ?? []).filter((c) => !selectedIds.has(c.id));

  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }}>
      <TextInput
        label="CI ara"
        type="search"
        hint="En az 2 karakter yazın; seçtiğiniz CI’lar için etki önizlemesi hesaplanır."
        value={term}
        autoComplete="off"
        onChange={(e) => {
          setTerm(e.target.value);
        }}
      />
      {q.length >= 2 &&
        (search.isPending ? (
          <Spinner label="Aranıyor…" />
        ) : search.isError ? (
          <p role="alert" className={styles.errorText}>
            CI araması başarısız oldu.
          </p>
        ) : results.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>
            Eşleşen CI yok.
          </p>
        ) : (
          <ul className={styles.pickList} aria-label="Arama sonuçları">
            {results.map((ci) => (
              <li key={ci.id}>
                <span>
                  <strong>{ci.name}</strong> <span className="muted">({describe(ci)})</span>
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  aria-label={`${ci.name} ekle`}
                  onClick={() => {
                    onChange([...selected, ci]);
                  }}
                >
                  Ekle
                </Button>
              </li>
            ))}
          </ul>
        ))}
      {selected.length > 0 ? (
        <ul className={styles.pickList} aria-label="Seçili CI’lar">
          {selected.map((ci) => (
            <li key={ci.id}>
              <span>
                <strong>{ci.name}</strong> <span className="muted">({describe(ci)})</span>
              </span>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`${ci.name} kaldır`}
                onClick={() => {
                  onChange(selected.filter((c) => c.id !== ci.id));
                }}
              >
                Kaldır
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          Henüz CI seçilmedi.
        </p>
      )}
    </div>
  );
}
