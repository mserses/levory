import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { queryKeys, requestsApi, type ServiceRequest } from '@/api';
import { EmptyState, ErrorState, Select, Spinner, buttonClassName } from '@/components';
import { formatDate } from '@/lib/format';
import { RequestStateBadge, TouchlessBadge } from './RequestBadges';
import styles from './Requests.module.css';
import { cx } from '@/lib/cx';

/** Tek istekte çekilen en fazla talep (backend üst sınırı 200) */
export const MY_SERVICE_REQUESTS_LIMIT = 200;
const LIST_QUERY = { mine: true, limit: MY_SERVICE_REQUESTS_LIMIT } as const;

const OPEN = new Set(['submitted', 'awaiting_approval', 'fulfilling']);

type Filter = 'open' | 'closed' | 'all';
const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'Tümü' },
  { value: 'open', label: 'Devam edenler' },
  { value: 'closed', label: 'Sonuçlananlar' },
];
const isFilter = (v: string): v is Filter => v === 'open' || v === 'closed' || v === 'all';

/** /taleplerim "Hizmet talepleri" sekmesi: GET /requests?mine=true */
export function ServiceRequestList({ canCreate }: { canCreate: boolean }) {
  const [filter, setFilter] = useState<Filter>('all');
  const query = useQuery({
    queryKey: queryKeys.requests.list(LIST_QUERY),
    queryFn: ({ signal }) => requestsApi.listMine({ limit: MY_SERVICE_REQUESTS_LIMIT }, signal),
  });
  const all = useMemo(
    () => [...(query.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [query.data],
  );
  const visible = all.filter((r) =>
    filter === 'all' ? true : filter === 'open' ? OPEN.has(r.state) : !OPEN.has(r.state),
  );

  if (query.isPending) return <Spinner block label="Hizmet talepleriniz yükleniyor…" showLabel />;
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        title="Hizmet talepleriniz yüklenemedi"
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (all.length === 0) {
    return (
      <EmptyState
        icon="🗂"
        title="Henüz hizmet talebiniz yok"
        description="Şifre sıfırlama, erişim, lisans gibi hizmetleri katalogdan isteyebilirsiniz."
        action={
          canCreate ? (
            <Link to="/katalog" className={buttonClassName('primary')}>
              Kataloğa git
            </Link>
          ) : undefined
        }
      />
    );
  }
  return (
    <div className="stack">
      <div className={styles.toolbar}>
        <Select
          label="Göster"
          options={FILTER_OPTIONS}
          value={filter}
          onChange={(e) => {
            if (isFilter(e.target.value)) setFilter(e.target.value);
          }}
        />
        <p className={styles.count} aria-live="polite">
          {visible.length} / {all.length} hizmet talebi gösteriliyor
        </p>
      </div>
      {visible.length === 0 ? (
        <EmptyState icon="✓" title="Bu görünümde hizmet talebi yok" />
      ) : (
        <div className={cx(styles.tableWrap, styles.listWrap)}>
          <table className={styles.table}>
            <caption className="visually-hidden">
              Hizmet talepleriniz; ayrıntı için hizmet adına tıklayın
            </caption>
            <thead>
              <tr>
                <th scope="col">Numara</th>
                <th scope="col">Hizmet</th>
                <th scope="col">Durum</th>
                <th scope="col">Açılış</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <Row key={r.id} request={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Row({ request }: { request: ServiceRequest }) {
  return (
    <tr>
      <td className={styles.number}>{request.number}</td>
      <th scope="row">
        <Link to={`/taleplerim/${encodeURIComponent(request.id)}`}>{request.item_name}</Link>
      </th>
      <td>
        <div className={styles.badges}>
          <RequestStateBadge state={request.state} />
          <TouchlessBadge request={request} />
        </div>
      </td>
      <td>{formatDate(request.created_at)}</td>
    </tr>
  );
}
