import { useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import {
  Button,
  EmptyState,
  ErrorState,
  PriorityBadge,
  Select,
  SlaBadge,
  Spinner,
  StatusBadge,
  TextInput,
  optionsFrom,
} from '@/components';
import { formatDateTime, formatRelative } from '@/lib/format';
import { PRIORITY_LABELS, STATUS_LABELS } from '@/lib/labels';
import { groupLabel } from '@/lib/taxonomy';
import { usePageTitle } from '@/lib/usePageTitle';
import { useIncidentList, useNow } from './hooks';
import styles from './IncidentQueuePage.module.css';
import {
  DEFAULT_FILTERS,
  PAGE_SIZE,
  hasActiveFilters,
  parseQueueParams,
  toApiQuery,
  toSearchParams,
  type QueueFilters,
  type QueueSort,
} from './queueParams';
import { sortBySlaDue } from './queueSort';

const SORT_OPTIONS = [
  { value: 'sla', label: 'SLA vadesi (en yakın önce)' },
  { value: 'newest', label: 'En yeni kayıt önce' },
] as const;

/** /kayitlar — agent iş kuyruğu (sahibi: incidents). GET /incidents */
export default function IncidentQueuePage() {
  usePageTitle('Kayıtlar');
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseQueueParams(params), [params]);
  const query = useMemo(() => toApiQuery(filters), [filters]);
  const list = useIncidentList(query);
  const now = useNow();
  const headingId = useId();

  const [assigneeDraft, setAssigneeDraft] = useState(filters.assignee);
  useEffect(() => {
    setAssigneeDraft(filters.assignee);
  }, [filters.assignee]);

  const update = (patch: Partial<QueueFilters>) => {
    // Filtre değişince ilk sayfaya dön; yalnızca sayfa değişiyorsa sayfayı koru
    const next = { ...filters, page: 1, ...patch };
    setParams(toSearchParams(next));
  };

  const rows = list.data ?? [];
  const hasNext = rows.length > PAGE_SIZE;
  const pageRows = rows.slice(0, PAGE_SIZE);
  const visible = filters.sort === 'sla' ? sortBySlaDue(pageRows, now) : pageRows;

  const onAssigneeSubmit = (e: FormEvent) => {
    e.preventDefault();
    update({ assignee: assigneeDraft.trim() });
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Kayıtlar</h1>
          <p className="muted" style={{ margin: 0 }}>
            Olay ve talep kuyruğu. Rozetler SLA durumunu gösterir: zamanında, risk altında, aşıldı
            ya da duraklatıldı.
          </p>
        </div>
      </div>

      <form
        className={`card ${styles.filters ?? ''}`}
        onSubmit={onAssigneeSubmit}
        role="search"
        aria-label="Kayıt filtreleri"
      >
        <Select
          label="Durum"
          placeholder="Tümü"
          options={optionsFrom(STATUS_LABELS)}
          value={filters.status ?? ''}
          onChange={(e) => {
            update({ status: (e.target.value || null) as QueueFilters['status'] });
          }}
        />
        <Select
          label="Öncelik"
          placeholder="Tümü"
          options={optionsFrom(PRIORITY_LABELS)}
          value={filters.priority ?? ''}
          onChange={(e) => {
            update({ priority: (e.target.value || null) as QueueFilters['priority'] });
          }}
        />
        <div className={styles.assignee}>
          <TextInput
            label="Atanan"
            hint="Kullanıcı adı; Enter ile uygulanır"
            value={assigneeDraft}
            autoComplete="off"
            onChange={(e) => {
              setAssigneeDraft(e.target.value);
            }}
          />
          <div className={styles.assigneeActions}>
            <Button type="submit" variant="secondary" size="sm">
              Uygula
            </Button>
            {user && (
              <Button
                variant="ghost"
                size="sm"
                aria-pressed={filters.assignee === user.username}
                onClick={() => {
                  update({ assignee: filters.assignee === user.username ? '' : user.username });
                }}
              >
                Bana atananlar
              </Button>
            )}
          </div>
        </div>
        <Select
          label="Sıralama"
          options={SORT_OPTIONS}
          value={filters.sort}
          onChange={(e) => {
            update({ sort: e.target.value as QueueSort, page: filters.page });
          }}
        />
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={filters.mine}
            onChange={(e) => {
              update({ mine: e.target.checked });
            }}
          />
          Yalnızca benim açtığım kayıtlar
        </label>
        {hasActiveFilters(filters) && (
          <Button
            variant="ghost"
            size="sm"
            className={styles.clear}
            onClick={() => {
              setParams(toSearchParams({ ...DEFAULT_FILTERS, sort: filters.sort }));
            }}
          >
            Filtreleri temizle
          </Button>
        )}
      </form>

      <section aria-labelledby={headingId} className="stack" aria-busy={list.isFetching}>
        <div className={styles.listHead}>
          <h2 id={headingId} className={styles.listTitle}>
            Sonuçlar
          </h2>
          <p className="muted" style={{ margin: 0 }} aria-live="polite">
            {list.isSuccess &&
              (pageRows.length === 0
                ? 'Kayıt yok'
                : `Sayfa ${filters.page} · ${pageRows.length} kayıt`)}
            {filters.sort === 'sla' &&
              list.isSuccess &&
              pageRows.length > 0 &&
              ' · SLA sıralaması bu sayfadaki kayıtlara uygulanır'}
          </p>
        </div>

        {list.isPending ? (
          <Spinner block label="Kayıtlar yükleniyor…" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : pageRows.length === 0 ? (
          <EmptyState
            title={filters.page > 1 ? 'Bu sayfada kayıt yok' : 'Kayıt bulunamadı'}
            description={
              hasActiveFilters(filters)
                ? 'Seçili filtrelere uyan kayıt yok. Filtreleri değiştirmeyi deneyin.'
                : 'Kuyruk şu an boş.'
            }
          />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className="visually-hidden">
                Kayıt kuyruğu, {filters.sort === 'sla' ? 'SLA vadesine' : 'oluşturulma zamanına'}{' '}
                göre sıralı
              </caption>
              <thead>
                <tr>
                  <th scope="col">Kayıt</th>
                  <th scope="col">Öncelik</th>
                  <th scope="col">Durum</th>
                  <th scope="col" aria-sort={filters.sort === 'sla' ? 'ascending' : 'none'}>
                    SLA
                  </th>
                  <th scope="col">Atanan</th>
                  <th scope="col" aria-sort={filters.sort === 'newest' ? 'descending' : 'none'}>
                    Oluşturulma
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((incident) => (
                  <tr key={incident.id} data-testid="incident-row">
                    <td data-label="Kayıt" className={styles.titleCell}>
                      <Link to={`/kayitlar/${encodeURIComponent(incident.id)}`}>
                        <span className={styles.number}>{incident.number}</span>
                        <span className={styles.title}>{incident.title}</span>
                      </Link>
                      <span className={styles.sub}>
                        {incident.caller}
                        {incident.assignment_group
                          ? ` · ${groupLabel(incident.assignment_group)}`
                          : ''}
                      </span>
                    </td>
                    <td data-label="Öncelik">
                      <PriorityBadge priority={incident.priority} />
                    </td>
                    <td data-label="Durum">
                      <StatusBadge
                        status={incident.status}
                        onHoldReason={incident.on_hold_reason}
                      />
                    </td>
                    <td data-label="SLA">
                      <SlaBadge incident={incident} now={now} />
                    </td>
                    <td data-label="Atanan">
                      {incident.assignee ?? <span className="muted">Atanmadı</span>}
                    </td>
                    <td data-label="Oluşturulma">
                      <time
                        dateTime={incident.created_at}
                        title={formatDateTime(incident.created_at)}
                      >
                        {formatRelative(incident.created_at, now)}
                      </time>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {list.isSuccess && (filters.page > 1 || hasNext) && (
          <nav className={styles.pager} aria-label="Sayfalama">
            <Button
              variant="secondary"
              size="sm"
              disabled={filters.page <= 1 || list.isFetching}
              onClick={() => {
                update({ page: filters.page - 1 });
              }}
            >
              ← Önceki sayfa
            </Button>
            <span className="muted">Sayfa {filters.page}</span>
            <Button
              variant="secondary"
              size="sm"
              disabled={!hasNext || list.isFetching}
              onClick={() => {
                update({ page: filters.page + 1 });
              }}
            >
              Sonraki sayfa →
            </Button>
          </nav>
        )}
      </section>
    </div>
  );
}
