import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { incidentsApi, queryKeys, type Incident, type IncidentStatus } from '@/api';
import { ROUTE_ROLES } from '@/app/navigation';
import { useAuth } from '@/auth/useAuth';
import {
  EmptyState,
  ErrorState,
  PriorityBadge,
  Select,
  SlaBadge,
  Spinner,
  StatusBadge,
  buttonClassName,
} from '@/components';
import { formatDate, formatDateTime } from '@/lib/format';
import { usePageTitle } from '@/lib/usePageTitle';
import { Drawer } from './components/Drawer';
import { RequestDetails } from './components/RequestDetails';
import styles from './MyRequestsPage.module.css';

/** Tek istekte çekilen en fazla kayıt (backend üst sınırı 500) */
export const MY_REQUESTS_LIMIT = 200;

const LIST_QUERY = { mine: true, limit: MY_REQUESTS_LIMIT } as const;

const CLOSED: ReadonlySet<IncidentStatus> = new Set(['resolved', 'closed', 'cancelled']);

type Filter = 'open' | 'closed' | 'all';
const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: 'open', label: 'Açık talepler' },
  { value: 'closed', label: 'Çözülen / kapanan' },
  { value: 'all', label: 'Tümü' },
];

function isFilter(v: string): v is Filter {
  return v === 'open' || v === 'closed' || v === 'all';
}

function byNewest(a: Incident, b: Incident) {
  return b.created_at.localeCompare(a.created_at);
}

/** /taleplerim — kullanıcının kendi kayıtları (sahibi: self-service). GET /incidents?mine=true */
export default function MyRequestsPage() {
  usePageTitle('Taleplerim');
  const { hasRole } = useAuth();
  const canCreate = hasRole(...ROUTE_ROLES.newRequest);
  const [filter, setFilter] = useState<Filter>('open');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: queryKeys.incidents.list(LIST_QUERY),
    queryFn: ({ signal }) => incidentsApi.listMine({ limit: MY_REQUESTS_LIMIT }, signal),
  });

  const all = useMemo(() => [...(query.data ?? [])].sort(byNewest), [query.data]);
  const visible = useMemo(
    () =>
      all.filter((i) =>
        filter === 'all' ? true : filter === 'open' ? !CLOSED.has(i.status) : CLOSED.has(i.status),
      ),
    [all, filter],
  );
  const selected = selectedId ? (all.find((i) => i.id === selectedId) ?? null) : null;

  const newRequestLink = canCreate ? (
    <Link to="/talep" className={buttonClassName('primary')}>
      Yeni talep oluştur
    </Link>
  ) : undefined;

  let content;
  if (query.isPending) {
    content = <Spinner block label="Talepleriniz yükleniyor…" showLabel />;
  } else if (query.isError) {
    content = (
      <ErrorState
        error={query.error}
        title="Talepleriniz yüklenemedi"
        onRetry={() => void query.refetch()}
      />
    );
  } else if (all.length === 0) {
    content = (
      <EmptyState
        icon="📭"
        title="Henüz bir talebiniz yok"
        description="Bir sorun yaşadığınızda veya bir şeye ihtiyaç duyduğunuzda buradan talep açabilirsiniz."
        action={newRequestLink}
      />
    );
  } else {
    content = (
      <>
        <div className={styles.toolbar}>
          <Select
            className={styles.filter}
            label="Göster"
            options={FILTER_OPTIONS}
            value={filter}
            onChange={(e) => {
              if (isFilter(e.target.value)) setFilter(e.target.value);
            }}
          />
          <p className={styles.count} aria-live="polite">
            {visible.length} / {all.length} talep gösteriliyor
            {all.length >= MY_REQUESTS_LIMIT && ` (en son ${MY_REQUESTS_LIMIT} talep)`}
          </p>
        </div>

        {visible.length === 0 ? (
          <EmptyState
            icon="✓"
            title={filter === 'open' ? 'Açık talebiniz yok' : 'Bu görünümde talep yok'}
            description="Diğer talepleri görmek için “Göster” seçimini değiştirin."
          />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className="visually-hidden">
                Talepleriniz; ayrıntı için başlığa tıklayın
              </caption>
              <thead>
                <tr>
                  <th scope="col">Numara</th>
                  <th scope="col">Başlık</th>
                  <th scope="col">Durum</th>
                  <th scope="col">Öncelik</th>
                  <th scope="col">Hedef süre</th>
                  <th scope="col">Açılış</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((incident) => (
                  <RequestRow
                    key={incident.id}
                    incident={incident}
                    onOpen={() => {
                      setSelectedId(incident.id);
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Taleplerim</h1>
          <p className="muted">Açtığınız talepler, durumları ve hedef çözüm süreleri.</p>
        </div>
        {all.length > 0 && newRequestLink}
      </div>

      {content}

      <Drawer
        open={selected !== null}
        onClose={() => {
          setSelectedId(null);
        }}
        title={selected?.title ?? ''}
        eyebrow={selected?.number}
      >
        {selected && <RequestDetails incident={selected} />}
      </Drawer>
    </div>
  );
}

function RequestRow({ incident, onOpen }: { incident: Incident; onOpen: () => void }) {
  const waitingForMe =
    incident.status === 'on_hold' && incident.on_hold_reason === 'awaiting_caller';
  const open = !CLOSED.has(incident.status);
  return (
    <tr>
      <td className={styles.number} data-label="Numara">
        {incident.number}
      </td>
      <th scope="row">
        <button
          type="button"
          className={styles.titleButton}
          onClick={onOpen}
          aria-haspopup="dialog"
          aria-label={`${incident.number} ${incident.title} — ayrıntıları aç`}
        >
          {incident.title}
        </button>
      </th>
      <td data-label="Durum">
        <StatusBadge status={incident.status} onHoldReason={incident.on_hold_reason} />
        {waitingForMe && <span className={styles.waiting}>Sizden yanıt bekleniyor</span>}
      </td>
      <td data-label="Öncelik">
        <PriorityBadge priority={incident.priority} />
      </td>
      <td data-label="Hedef süre">
        <SlaBadge incident={incident} />
        {open && incident.resolve_due_at && (
          <span className={styles.due}>Çözüm: {formatDateTime(incident.resolve_due_at)}</span>
        )}
      </td>
      <td data-label="Açılış">{formatDate(incident.created_at)}</td>
    </tr>
  );
}
