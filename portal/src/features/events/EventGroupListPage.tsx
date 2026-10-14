import { useQueries, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { cmdbApi, eventsApi, queryKeys, type EventGroup, type EventGroupListQuery } from '@/api';
import type { EventGroupState } from '@/api/types';
import { EmptyState, ErrorState, Select, Spinner, optionsFrom } from '@/components';
import { formatRelative } from '@/lib/format';
import { EVENT_GROUP_STATE_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { GroupStatus, LinkedIncident, SeverityBadge } from './GroupStatus';
import styles from './Events.module.css';
import { cx } from '@/lib/cx';

const LIMIT = 100;
const isState = (v: string): v is EventGroupState => v in EVENT_GROUP_STATE_LABELS;

/** /olaylar — bağıntılı izleme uyarıları (sahibi: events). GET /events/groups */
export default function EventGroupListPage() {
  usePageTitle('Olaylar');
  const [state, setState] = useState<EventGroupState | ''>('open');
  const query: EventGroupListQuery = { limit: LIMIT, ...(state ? { state } : {}) };
  const groups = useQuery({
    queryKey: queryKeys.events.groups(query),
    queryFn: ({ signal }) => eventsApi.listGroups(query, signal),
  });

  let content;
  if (groups.isPending) content = <Spinner block label="Uyarı grupları yükleniyor…" showLabel />;
  else if (groups.isError) {
    content = (
      <ErrorState
        error={groups.error}
        title="Uyarı grupları yüklenemedi"
        onRetry={() => void groups.refetch()}
      />
    );
  } else if (groups.data.items.length === 0) {
    content = (
      <EmptyState
        icon="✓"
        title={state === 'open' ? 'Açık uyarı grubu yok' : 'Bu süzgeçle uyarı grubu yok'}
        description="İzleme sistemlerinden (Alertmanager, genel webhook) gelen uyarılar CMDB bağımlılıklarına göre gruplanır."
      />
    );
  } else {
    content = <GroupTable groups={groups.data.items} />;
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Olaylar</h1>
          <p className="muted" style={{ margin: 0 }}>
            İzleme uyarıları kök CI altında bağıntılanır; grup başına en fazla bir olay kaydı
            açılır. Susturma her zaman insan onayı ister.
          </p>
        </div>
      </header>
      <div className={styles.toolbar}>
        <Select
          label="Durum"
          placeholder="Tüm durumlar"
          options={optionsFrom(EVENT_GROUP_STATE_LABELS)}
          value={state}
          onChange={(e) => {
            const v = e.target.value;
            setState(isState(v) ? v : '');
          }}
        />
      </div>
      {content}
    </div>
  );
}

function GroupTable({ groups }: { groups: readonly EventGroup[] }) {
  const ciIds = [...new Set(groups.map((g) => g.root_ci_id).filter((v): v is string => !!v))];
  const cis = useQueries({
    queries: ciIds.map((id) => ({
      queryKey: queryKeys.cmdb.detail(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => cmdbApi.get(id, signal),
      staleTime: 5 * 60_000,
    })),
  });
  const ciName = (id: string) => cis[ciIds.indexOf(id)]?.data?.name ?? 'CMDB kaydı';
  return (
    <>
      <p className={styles.count} aria-live="polite">
        {groups.length} uyarı grubu{groups.length >= LIMIT && ` (en yeni ${String(LIMIT)})`}
      </p>
      <div className={cx(styles.tableWrap, styles.listWrap)}>
        <table className={styles.table}>
          <caption className="visually-hidden">Uyarı grupları</caption>
          <thead>
            <tr>
              <th scope="col">Numara</th>
              <th scope="col">Başlık</th>
              <th scope="col">Önem</th>
              <th scope="col">Kök CI</th>
              <th scope="col">Durum</th>
              <th scope="col">Olay kaydı</th>
              <th scope="col">Uyarı</th>
              <th scope="col">Son uyarı</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id}>
                <td className={styles.number}>{g.number}</td>
                <th scope="row">
                  <Link to={`/olaylar/${encodeURIComponent(g.id)}`}>{g.title}</Link>
                </th>
                <td>
                  <SeverityBadge severity={g.severity} />
                </td>
                <td>
                  {g.root_ci_id ? (
                    <Link to={`/cmdb/${encodeURIComponent(g.root_ci_id)}`}>
                      {ciName(g.root_ci_id)}
                    </Link>
                  ) : (
                    <span className="muted">Eşlenmedi</span>
                  )}
                </td>
                <td>
                  <GroupStatus group={g} />
                </td>
                <td>
                  <LinkedIncident group={g} />
                </td>
                <td>{g.event_count}</td>
                <td>{formatRelative(g.last_event_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
