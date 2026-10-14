import { useQueries } from '@tanstack/react-query';
import { useId, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { changesApi, queryKeys } from '@/api';
import type { Change } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import {
  EmptyState,
  ErrorState,
  Select,
  Spinner,
  buttonClassName,
  optionsFrom,
} from '@/components';
import { formatDateTime } from '@/lib/format';
import { CHANGE_STATE_LABELS, CHANGE_TYPE_LABELS, RISK_LEVEL_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { dayKey } from './calendar';
import styles from './Changes.module.css';
import { CalendarView } from './components/CalendarView';
import { ChangeStateBadge, ChangeTypeBadge, RiskBadge } from './components/ChangeBadges';
import { SegmentedControl } from './components/SegmentedControl';
import { useChangeList } from './hooks';
import {
  anchorFromParam,
  parseChangeParams,
  toChangeParams,
  toChangeQuery,
  type ChangeFilters,
} from './listParams';
import { isAwaitingMyVote } from './rules';

/** /degisiklikler — değişiklik listesi ve takvimi (sahibi: changes). GET /changes, /calendar */
export default function ChangeListPage() {
  usePageTitle('Değişiklikler');
  const { user, roles, hasRole } = useAuth();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseChangeParams(params), [params]);
  const canCreate = hasRole('agent', 'change_manager');
  const canVote = hasRole('cab', 'change_manager');
  const update = (patch: Partial<ChangeFilters>) => {
    setParams(toChangeParams({ ...filters, ...patch }), { replace: true });
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Değişiklikler</h1>
          <p className="muted" style={{ margin: 0 }}>
            Değişiklik talepleri (RFC), CAB onayları ve değişiklik takvimi.
          </p>
        </div>
        <div className="row">
          <SegmentedControl
            legend="Görünüm"
            value={filters.view}
            options={[
              { value: 'list', label: 'Liste' },
              { value: 'calendar', label: 'Takvim' },
            ]}
            onChange={(view) => {
              update({ view });
            }}
          />
          {canCreate && (
            <Link to="/degisiklikler/yeni" className={buttonClassName('primary')}>
              Yeni değişiklik
            </Link>
          )}
        </div>
      </header>
      {filters.view === 'calendar' ? (
        <CalendarView
          mode={filters.calendarMode}
          anchor={anchorFromParam(filters.date)}
          onModeChange={(calendarMode) => {
            update({ calendarMode });
          }}
          onAnchorChange={(anchor) => {
            update({ date: dayKey(anchor) });
          }}
        />
      ) : (
        <ChangeList
          filters={filters}
          update={update}
          canVote={canVote}
          actor={{ username: user?.username ?? '', roles }}
        />
      )}
    </div>
  );
}

function ChangeList({
  filters,
  update,
  canVote,
  actor,
}: {
  filters: ChangeFilters;
  update: (patch: Partial<ChangeFilters>) => void;
  canVote: boolean;
  actor: { username: string; roles: string[] };
}) {
  const headingId = useId();
  const awaitingVote = canVote && filters.awaitingVote;
  const list = useChangeList(toChangeQuery({ ...filters, awaitingVote }));
  const changes = useMemo(() => list.data ?? [], [list.data]);

  // "Oyumu bekleyenler": her yetkilendirmedeki değişikliğin oylarını getir (az sayıda)
  const candidates = awaitingVote ? changes.filter((c) => c.state === 'authorize') : [];
  const approvals = useQueries({
    queries: candidates.map((c) => ({
      queryKey: queryKeys.changes.approvals(c.id),
      queryFn: ({ signal }: { signal: AbortSignal }) => changesApi.approvals(c.id, signal),
    })),
  });
  const approvalsPending = approvals.some((q) => q.isPending);

  let visible: Change[] = changes;
  if (awaitingVote) {
    visible = candidates.filter((c, i) => {
      const items = approvals[i]?.data?.items;
      return items !== undefined && isAwaitingMyVote(c, items, actor);
    });
  }
  if (filters.risk) visible = visible.filter((c) => c.risk_level === filters.risk);

  return (
    <>
      <section className="card" aria-label="Süzgeçler">
        <div className={styles.filters}>
          <Select
            label="Durum"
            placeholder="Tümü"
            options={optionsFrom(CHANGE_STATE_LABELS)}
            value={awaitingVote ? 'authorize' : filters.state}
            disabled={awaitingVote}
            onChange={(e) => {
              update({ state: e.target.value as ChangeFilters['state'] });
            }}
          />
          <Select
            label="Tür"
            placeholder="Tümü"
            options={optionsFrom(CHANGE_TYPE_LABELS)}
            value={filters.type}
            disabled={awaitingVote}
            onChange={(e) => {
              update({ type: e.target.value as ChangeFilters['type'] });
            }}
          />
          <Select
            label="Risk"
            placeholder="Tümü"
            options={optionsFrom(RISK_LEVEL_LABELS)}
            value={filters.risk}
            onChange={(e) => {
              update({ risk: e.target.value as ChangeFilters['risk'] });
            }}
          />
          <div className={styles.checks}>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={filters.mine}
                disabled={awaitingVote}
                onChange={(e) => {
                  update({ mine: e.target.checked });
                }}
              />
              Yalnızca benim taleplerim
            </label>
            {canVote && (
              <label className={styles.check}>
                <input
                  type="checkbox"
                  checked={filters.awaitingVote}
                  onChange={(e) => {
                    update({ awaitingVote: e.target.checked });
                  }}
                />
                CAB oyumu bekleyenler
              </label>
            )}
          </div>
        </div>
      </section>
      <section className="stack" aria-labelledby={headingId}>
        <h2 id={headingId} className={styles.sectionTitle}>
          {awaitingVote ? 'Oyunuzu bekleyen değişiklikler' : 'Değişiklik listesi'}
          {!list.isPending && !approvalsPending && (
            <span className="muted"> ({visible.length})</span>
          )}
        </h2>
        {list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.isPending || (awaitingVote && approvalsPending) ? (
          <Spinner block label="Değişiklikler yükleniyor…" />
        ) : visible.length === 0 ? (
          <EmptyState
            title="Değişiklik bulunamadı"
            description={
              awaitingVote
                ? 'Oyunuzu bekleyen değişiklik yok.'
                : 'Süzgeçleri değiştirin ya da yeni bir değişiklik talebi açın.'
            }
            headingLevel={3}
          />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Değişiklik</th>
                  <th scope="col">Tür</th>
                  <th scope="col">Durum</th>
                  <th scope="col">Risk</th>
                  <th scope="col">Planlanan pencere</th>
                  <th scope="col">Talep eden</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((c) => (
                  <tr key={c.id} data-testid="change-row">
                    <td className={styles.titleCell}>
                      <Link to={`/degisiklikler/${encodeURIComponent(c.id)}`}>
                        <span className="muted">{c.number}</span>
                        <span>{c.title}</span>
                      </Link>
                    </td>
                    <td>
                      <ChangeTypeBadge type={c.change_type} />
                    </td>
                    <td>
                      <ChangeStateBadge state={c.state} />
                    </td>
                    <td>
                      <RiskBadge level={c.risk_level} score={c.risk_score} />
                    </td>
                    <td>
                      {c.planned_start ? (
                        <>
                          {formatDateTime(c.planned_start)}
                          <br />
                          <span className="muted">→ {formatDateTime(c.planned_end)}</span>
                        </>
                      ) : (
                        <span className="muted">Planlanmadı</span>
                      )}
                    </td>
                    <td>{c.requested_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
