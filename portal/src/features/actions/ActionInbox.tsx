import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { actionsApi, queryKeys, type ActionRequestState } from '@/api';
import { EmptyState, ErrorState, Select, Spinner } from '@/components';
import { ACTION_STATE_LABELS } from '@/lib/labels';
import { ActionCard } from './ActionCard';
import styles from './Actions.module.css';

export const ACTION_PAGE_SIZE = 50;

type Filter = ActionRequestState | 'all';
const FILTERS: { value: Filter; label: string }[] = [
  { value: 'pending', label: ACTION_STATE_LABELS.pending },
  { value: 'executed', label: ACTION_STATE_LABELS.executed },
  { value: 'rejected', label: ACTION_STATE_LABELS.rejected },
  { value: 'failed', label: ACTION_STATE_LABELS.failed },
  { value: 'expired', label: ACTION_STATE_LABELS.expired },
  { value: 'all', label: 'Tümü' },
];
const isFilter = (v: string): v is Filter => FILTERS.some((f) => f.value === v);

interface Props {
  /** /onaylar?sekme=eylem&istek=<id>: talep ayrıntısından gelinen istek en üstte vurgulanır */
  focusId?: string | null;
}

/** /onaylar "Eylem onayları" sekmesi — R2/R3 (ve politikanın onaya düşürdüğü R1) araç istekleri */
export function ActionInbox({ focusId = null }: Props) {
  const [filter, setFilter] = useState<Filter>('pending');
  const listQuery = { ...(filter === 'all' ? {} : { state: filter }), limit: ACTION_PAGE_SIZE };
  const query = useQuery({
    queryKey: queryKeys.actions.list(listQuery),
    queryFn: ({ signal }) => actionsApi.list(listQuery, signal),
  });
  const inList = query.data?.some((a) => a.id === focusId) ?? false;
  const focused = useQuery({
    queryKey: queryKeys.actions.detail(focusId ?? ''),
    queryFn: ({ signal }) => actionsApi.get(focusId ?? '', signal),
    enabled: focusId !== null && query.isSuccess && !inList,
  });

  const items = query.data ?? [];

  return (
    <section className="stack" aria-labelledby="eylem-onaylari">
      <div className={styles.toolbar}>
        <div>
          <h2 id="eylem-onaylari" style={{ margin: 0 }}>
            Eylem onayları
          </h2>
          <p className="muted" style={{ margin: 0 }}>
            Otomasyonun insan onayı olmadan çalıştıramayacağı eylemler. R2 tek onay, R3 iki farklı
            onaylayan (ya da CAB onaylı bağlı değişiklik) ister. Onaylanan eylem sizin kimliğinizle
            yürütülür.
          </p>
        </div>
        <Select
          label="Durum"
          options={FILTERS}
          value={filter}
          onChange={(e) => {
            if (isFilter(e.target.value)) setFilter(e.target.value);
          }}
        />
      </div>

      {focusId && !inList && focused.data && (
        <div className="stack" style={{ gap: 'var(--space-2)' }}>
          <p className={styles.count}>Bağlantıyla açılan istek</p>
          <ActionCard action={focused.data} highlight />
        </div>
      )}
      {focusId && !inList && focused.isError && (
        <ErrorState error={focused.error} title="Bağlantıdaki eylem isteği yüklenemedi" />
      )}

      {query.isPending ? (
        <Spinner block label="Eylem istekleri yükleniyor…" showLabel />
      ) : query.isError ? (
        <ErrorState
          error={query.error}
          title="Eylem istekleri yüklenemedi"
          onRetry={() => void query.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon="✓"
          headingLevel={3}
          title={filter === 'pending' ? 'Onay bekleyen eylem yok' : 'Bu durumda eylem isteği yok'}
          description="Otomasyon riskli bir eylem istediğinde burada görünür."
        />
      ) : (
        <>
          <p className={styles.count} aria-live="polite">
            {items.length} eylem isteği
            {items.length >= ACTION_PAGE_SIZE && ` (en yeni ${String(ACTION_PAGE_SIZE)})`}
          </p>
          <ul className={styles.list}>
            {items.map((a) => (
              <li key={a.id}>
                <ActionCard action={a} highlight={a.id === focusId} />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
