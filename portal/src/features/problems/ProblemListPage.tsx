import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  errorMessage,
  problemsApi,
  queryKeys,
  type AgentResults,
  type Problem,
  type ProblemListQuery,
  type ProblemState,
  type Trends,
} from '@/api';
import { ROUTE_ROLES } from '@/app/navigation';
import { useAuth } from '@/auth/useAuth';
import {
  Badge,
  Button,
  ConfidenceMeter,
  EmptyState,
  ErrorState,
  PriorityBadge,
  Select,
  Spinner,
  TextInput,
  optionsFrom,
  useToast,
} from '@/components';
import { formatDate } from '@/lib/format';
import { PROBLEM_STATE_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { ProblemStateBadge } from './ProblemStateBadge';
import { TREND_KIND_LABELS, agentOutcome } from './model';
import styles from './Problems.module.css';
import { cx } from '@/lib/cx';

const LIMIT = 100;
const WINDOWS = [
  { value: '7', label: 'Son 7 gün' },
  { value: '14', label: 'Son 14 gün' },
  { value: '30', label: 'Son 30 gün' },
];
const isProblemState = (v: string): v is ProblemState => v in PROBLEM_STATE_LABELS;

/** /problemler — sorun kayıtları + eğilim analizi (sahibi: problems). GET /problems */
export default function ProblemListPage() {
  usePageTitle('Sorunlar');
  const [state, setState] = useState<ProblemState | ''>('');
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const listQuery: ProblemListQuery = {
    ...(state ? { state } : {}),
    ...(q ? { q } : {}),
    limit: LIMIT,
  };
  const query = useQuery({
    queryKey: queryKeys.problems.list(listQuery),
    queryFn: ({ signal }) => problemsApi.list(listQuery, signal),
  });

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    setQ(search.trim());
  };

  let content;
  if (query.isPending) content = <Spinner block label="Sorunlar yükleniyor…" showLabel />;
  else if (query.isError) {
    content = (
      <ErrorState
        error={query.error}
        title="Sorunlar yüklenemedi"
        onRetry={() => void query.refetch()}
      />
    );
  } else if (query.data.length === 0) {
    content = (
      <EmptyState
        icon="🧩"
        title={state || q ? 'Bu süzgeçle sorun yok' : 'Henüz sorun kaydı yok'}
        description="Tekrarlayan olaylar için aşağıdaki eğilim analizini çalıştırabilirsiniz."
      />
    );
  } else {
    content = <ProblemTable problems={query.data} />;
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Sorunlar</h1>
          <p className="muted" style={{ margin: 0 }}>
            Tekrarlayan olayların kök nedeni: inceleme, geçici çözüm, bilinen hata ve kalıcı çözüm.
          </p>
        </div>
      </header>

      <form className={styles.toolbar} onSubmit={onSearch} role="search" aria-label="Sorun ara">
        <TextInput
          type="search"
          label="Numara ya da başlık"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
          }}
        />
        <Select
          label="Durum"
          placeholder="Tüm durumlar"
          options={optionsFrom(PROBLEM_STATE_LABELS)}
          value={state}
          onChange={(e) => {
            const v = e.target.value;
            setState(isProblemState(v) ? v : '');
          }}
        />
        <Button type="submit" variant="secondary">
          Ara
        </Button>
      </form>

      {content}

      <TrendPanel />
    </div>
  );
}

function ProblemTable({ problems }: { problems: readonly Problem[] }) {
  return (
    <>
      <p className={styles.count} aria-live="polite">
        {problems.length} sorun
        {problems.length >= LIMIT && ` (en yeni ${String(LIMIT)})`}
      </p>
      <div className={cx(styles.tableWrap, styles.listWrap)}>
        <table className={styles.table}>
          <caption className="visually-hidden">Sorun kayıtları</caption>
          <thead>
            <tr>
              <th scope="col">Numara</th>
              <th scope="col">Başlık</th>
              <th scope="col">Durum</th>
              <th scope="col">Öncelik</th>
              <th scope="col">Olay</th>
              <th scope="col">Güncelleme</th>
            </tr>
          </thead>
          <tbody>
            {problems.map((p) => (
              <tr key={p.id}>
                <td className={styles.number}>{p.number}</td>
                <th scope="row">
                  <Link to={`/problemler/${encodeURIComponent(p.id)}`}>{p.title}</Link>
                  {p.detected_by.startsWith('ai:') && (
                    <div>
                      <Badge tone="accent">Yapay zekâ tespiti</Badge>
                    </div>
                  )}
                </th>
                <td>
                  <ProblemStateBadge state={p.state} />
                </td>
                <td>
                  <PriorityBadge priority={p.priority} />
                </td>
                <td>{p.incident_ids.length}</td>
                <td>{formatDate(p.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Eğilim analizi: POST /problems/trends (salt okuma) ve /trends/propose (araç boru hattı) */
function TrendPanel() {
  const [windowDays, setWindowDays] = useState('14');
  const [proposed, setProposed] = useState<AgentResults | null>(null);
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const scan = useMutation({
    mutationFn: (): Promise<Trends> => problemsApi.trends(Number(windowDays)),
    onSuccess: () => {
      setProposed(null);
    },
  });
  const propose = useMutation({
    mutationFn: () => problemsApi.proposeFromTrends(Number(windowDays)),
    onSuccess: (res) => {
      setProposed(res);
      void queryClient.invalidateQueries({ queryKey: queryKeys.problems.lists });
    },
    onError: (e) => {
      toast.show({ title: 'Öneri gönderilemedi', description: errorMessage(e), tone: 'error' });
    },
  });
  const clusters = scan.data?.clusters ?? [];
  const summary = proposed?.results.map(agentOutcome) ?? [];
  const created = summary.filter((s) => s.kind === 'executed').length;
  const pending = summary.filter((s) => s.kind === 'pending_approval').length;
  const failed = summary.filter((s) => s.kind === 'error').length;

  return (
    <section className="card stack" aria-labelledby="egilim-analizi">
      <h2 id="egilim-analizi" className={styles.sectionHeading}>
        Eğilim analizi
      </h2>
      <p className="muted" style={{ margin: 0 }}>
        Aynı CI ve kategoride ya da benzer başlıkta tekrarlayan olayları kümeler. Taramak bir şey
        yazmaz; öneri, politika izin vermezse onay kutusuna düşer.
      </p>
      <div className={styles.toolbar}>
        <Select
          label="Dönem"
          options={WINDOWS}
          value={windowDays}
          onChange={(e) => {
            setWindowDays(e.target.value);
          }}
        />
        <Button
          variant="secondary"
          loading={scan.isPending}
          onClick={() => {
            scan.mutate();
          }}
        >
          Eğilimleri tara
        </Button>
      </div>
      {scan.isError && <ErrorState error={scan.error} title="Eğilim analizi yapılamadı" />}
      {scan.isSuccess && clusters.length === 0 && (
        <p className={styles.meta} role="status">
          Bu dönemde tekrarlayan olay kümesi bulunamadı.
        </p>
      )}
      {clusters.length > 0 && (
        <>
          <p className={styles.meta} role="status">
            {clusters.length} küme bulundu.
          </p>
          <ul className={styles.clusters}>
            {clusters.map((c) => (
              <li key={c.key} className={styles.cluster}>
                <h3 className={styles.clusterTitle}>{c.title}</h3>
                <div className={styles.badges}>
                  <Badge tone="info">{TREND_KIND_LABELS[c.kind] ?? 'Küme'}</Badge>
                  <Badge>{c.size} olay</Badge>
                  <PriorityBadge priority={c.priority} />
                </div>
                <p className={styles.meta}>Olaylar: {c.incident_numbers.join(', ')}</p>
                <ConfidenceMeter value={c.confidence} />
              </li>
            ))}
          </ul>
          <div>
            <Button
              loading={propose.isPending}
              onClick={() => {
                propose.mutate();
              }}
            >
              Sorun kayıtları öner
            </Button>
          </div>
        </>
      )}
      {proposed && (
        <p className={styles.notice} role="status">
          {created} sorun kaydı açıldı, {pending} öneri onay bekliyor
          {failed > 0 && `, ${String(failed)} öneri başarısız`}.{' '}
          {pending > 0 && hasRole(...ROUTE_ROLES.approvals) && (
            <Link to="/onaylar?sekme=eylem">Eylem onaylarına git</Link>
          )}
        </p>
      )}
    </section>
  );
}
