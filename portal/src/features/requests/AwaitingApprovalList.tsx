import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { AWAITING_MY_APPROVAL_QUERY, queryKeys, requestsApi } from '@/api';
import { EmptyState, ErrorState, Spinner } from '@/components';
import { formatDate } from '@/lib/format';
import styles from './Requests.module.css';
import { cx } from '@/lib/cx';

/** /taleplerim "Onayımı bekleyenler" sekmesi: GET /requests?awaiting_my_approval=true */
export function AwaitingApprovalList() {
  const query = useQuery({
    queryKey: queryKeys.requests.list(AWAITING_MY_APPROVAL_QUERY),
    queryFn: ({ signal }) => requestsApi.listAwaitingMyApproval(signal),
  });
  const rows = useMemo(
    () => [...(query.data ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at)),
    [query.data],
  );

  if (query.isPending)
    return <Spinner block label="Onay bekleyen talepler yükleniyor…" showLabel />;
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        title="Onay bekleyen talepler yüklenemedi"
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon="✓"
        title="İş onayınızı bekleyen talep yok"
        description="Ekibinizden biri yönetici onayı gereken bir hizmet istediğinde burada görünür."
      />
    );
  }
  return (
    <div className={cx(styles.tableWrap, styles.listWrap)}>
      <table className={styles.table}>
        <caption className="visually-hidden">
          İş onayınızı bekleyen hizmet talepleri (en eski önce); karar için hizmet adına tıklayın
        </caption>
        <thead>
          <tr>
            <th scope="col">Numara</th>
            <th scope="col">Hizmet</th>
            <th scope="col">Talep eden</th>
            <th scope="col">Açılış</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className={styles.number}>{r.number}</td>
              <th scope="row">
                <Link to={`/taleplerim/${encodeURIComponent(r.id)}`}>{r.item_name}</Link>
              </th>
              <td>{r.requester}</td>
              <td>{formatDate(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
