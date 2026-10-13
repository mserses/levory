import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  errorMessage,
  incidentsApi,
  isApiError,
  problemsApi,
  queryKeys,
  type Incident,
  type Problem,
  type ProblemState,
} from '@/api';
import { ROUTE_ROLES } from '@/app/navigation';
import { useAuth } from '@/auth/useAuth';
import {
  Badge,
  Button,
  ConfirmDialog,
  Dialog,
  ErrorState,
  PriorityBadge,
  Select,
  Spinner,
  StatusBadge,
  TextArea,
  TextInput,
  buttonClassName,
  useToast,
} from '@/components';
import { formatDate, formatDateTime } from '@/lib/format';
import { PROBLEM_STATE_LABELS, actorLabel } from '@/lib/labels';
import { categoryLabel, groupLabel } from '@/lib/taxonomy';
import { usePageTitle } from '@/lib/usePageTitle';
import { ProblemStateBadge } from './ProblemStateBadge';
import { useProblemWrite } from './useProblemWrite';
import { RcaSection } from './RcaSection';
import { PROBLEM_TRANSITIONS, agentOutcome, type AgentOutcome } from './model';
import styles from './Problems.module.css';

/** /problemler/:id — sorun ayrıntısı (sahibi: problems). GET /problems/{id} */
export default function ProblemDetailPage() {
  const { id = '' } = useParams();
  const query = useQuery({
    queryKey: queryKeys.problems.detail(id),
    queryFn: ({ signal }) => problemsApi.get(id, signal),
  });
  usePageTitle(query.data ? `${query.data.number} · ${query.data.title}` : 'Sorun');

  if (query.isPending) {
    return (
      <div className="page">
        <Spinner block label="Sorun yükleniyor…" showLabel />
      </div>
    );
  }
  if (query.isError) {
    const notFound = isApiError(query.error) && query.error.isNotFound;
    return (
      <div className="page">
        <h1>Sorun</h1>
        <ErrorState
          error={query.error}
          title={notFound ? 'Sorun bulunamadı' : 'Sorun yüklenemedi'}
          {...(notFound ? {} : { onRetry: () => void query.refetch() })}
          action={
            <Link to="/problemler" className={buttonClassName('secondary')}>
              Sorunlara dön
            </Link>
          }
        />
      </div>
    );
  }
  return <ProblemDetail problem={query.data} />;
}

function ProblemDetail({ problem }: { problem: Problem }) {
  const closed = problem.state === 'closed';
  return (
    <div className="page">
      <header className="page-header">
        <div className="stack" style={{ gap: 'var(--space-2)' }}>
          <p className="muted" style={{ margin: 0 }}>
            <Link to="/problemler">Sorunlar</Link> · {problem.number}
          </p>
          <h1>
            {problem.number} · {problem.title}
          </h1>
          <div className={styles.badges}>
            <ProblemStateBadge state={problem.state} />
            <PriorityBadge priority={problem.priority} />
            {problem.detected_by.startsWith('ai:') && (
              <Badge tone="accent">Yapay zekâ tespiti</Badge>
            )}
          </div>
        </div>
      </header>

      <div className={styles.layout}>
        <div className="stack">
          <Evidence problem={problem} />
          <RcaSection problem={problem} />
          <Workaround problem={problem} />
          <Fix problem={problem} />
        </div>
        <div className="stack">
          <section className="card" aria-labelledby="sorun-ozet">
            <h2
              id="sorun-ozet"
              className={styles.sectionHeading}
              style={{ marginBottom: 'var(--space-3)' }}
            >
              Özet
            </h2>
            <dl className={styles.facts}>
              <dt>Kategori</dt>
              <dd>{problem.category ? categoryLabel(problem.category) : '—'}</dd>
              <dt>Atanan grup</dt>
              <dd>{problem.assignment_group ? groupLabel(problem.assignment_group) : '—'}</dd>
              <dt>Tespit eden</dt>
              <dd>{actorLabel(problem.detected_by)}</dd>
              <dt>Açılış</dt>
              <dd>{formatDateTime(problem.created_at)}</dd>
              <dt>Güncelleme</dt>
              <dd>{formatDateTime(problem.updated_at)}</dd>
              {problem.root_cause && (
                <>
                  <dt>Kök neden</dt>
                  <dd className="prewrap">{problem.root_cause}</dd>
                </>
              )}
              {problem.resolution && (
                <>
                  <dt>Kapanış notu</dt>
                  <dd className="prewrap">{problem.resolution}</dd>
                </>
              )}
            </dl>
          </section>
          {!closed && <Transition problem={problem} />}
        </div>
      </div>
    </div>
  );
}

/** Eğilim kanıtı: açıklama (ajanın eğilim özeti) + bağlı olaylar, zaman aralığı, ortak alanlar */
function Evidence({ problem }: { problem: Problem }) {
  const { hasRole } = useAuth();
  const canOpenIncidents = hasRole(...ROUTE_ROLES.incidents);
  const write = useProblemWrite();
  const incidents = useQueries({
    queries: problem.incident_ids.map((id) => ({
      queryKey: queryKeys.incidents.detail(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => incidentsApi.get(id, signal),
      staleTime: 60_000,
    })),
  });
  const loaded = incidents.map((q) => q.data).filter((i): i is Incident => i !== undefined);
  const dates = loaded.map((i) => i.created_at).sort();
  const categories = new Set(loaded.map((i) => i.category).filter(Boolean));
  const unlink = useMutation({
    mutationFn: (incidentId: string) => problemsApi.unlinkIncident(problem.id, incidentId),
    onSuccess: (p) => {
      write.onSuccess(p, 'Olay bağı kaldırıldı');
    },
    onError: (e) => {
      write.onError(problem.id, 'Olay bağı kaldırılamadı', e);
    },
  });

  return (
    <section className="card stack" aria-labelledby="sorun-kanit">
      <h2 id="sorun-kanit" className={styles.sectionHeading}>
        Eğilim kanıtı ve bağlı olaylar
      </h2>
      {problem.description && (
        <p className="prewrap" style={{ margin: 0 }}>
          {problem.description}
        </p>
      )}
      {problem.incident_ids.length === 0 ? (
        <p className={styles.meta}>Bağlı olay yok.</p>
      ) : (
        <>
          <p className={styles.meta} data-testid="evidence-summary">
            {problem.incident_ids.length} bağlı olay
            {dates.length > 1 &&
              ` · ${formatDate(dates[0])} – ${formatDate(dates[dates.length - 1])}`}
            {categories.size === 1 &&
              loaded.length === problem.incident_ids.length &&
              ` · ortak kategori: ${categoryLabel([...categories][0] ?? '')}`}
            {problem.affected_ci_ids.length > 0 &&
              ` · ${String(problem.affected_ci_ids.length)} etkilenen CI`}
          </p>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className="visually-hidden">Bağlı olaylar</caption>
              <thead>
                <tr>
                  <th scope="col">Numara</th>
                  <th scope="col">Başlık</th>
                  <th scope="col">Durum</th>
                  <th scope="col">Açılış</th>
                  <th scope="col">
                    <span className="visually-hidden">İşlem</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {problem.incident_ids.map((id, i) => {
                  const q = incidents[i];
                  const inc = q?.data;
                  return (
                    <tr key={id}>
                      <td className={styles.number}>{inc?.number ?? '…'}</td>
                      <th scope="row">
                        {inc ? (
                          canOpenIncidents ? (
                            <Link to={`/kayitlar/${encodeURIComponent(id)}`}>{inc.title}</Link>
                          ) : (
                            inc.title
                          )
                        ) : q?.isError ? (
                          'Olay yüklenemedi'
                        ) : (
                          'Yükleniyor…'
                        )}
                      </th>
                      <td>{inc && <StatusBadge status={inc.status} />}</td>
                      <td>{inc ? formatDate(inc.created_at) : ''}</td>
                      <td>
                        {problem.state !== 'closed' && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={unlink.isPending}
                            aria-label={`${inc?.number ?? 'Olay'} bağını kaldır`}
                            onClick={() => {
                              unlink.mutate(id);
                            }}
                          >
                            Bağı kaldır
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

/** Geçici çözüm (PATCH, If-Match) + bilinen hata olarak yayımlama (insan) */
function Workaround({ problem }: { problem: Problem }) {
  const [text, setText] = useState(problem.workaround ?? '');
  const [confirm, setConfirm] = useState(false);
  const write = useProblemWrite();
  const closed = problem.state === 'closed';
  const dirty = text.trim() !== (problem.workaround ?? '').trim();
  const save = useMutation({
    mutationFn: () => problemsApi.update(problem.id, problem.version, { workaround: text.trim() }),
    onSuccess: (p) => {
      write.onSuccess(p, 'Geçici çözüm kaydedildi');
    },
    onError: (e) => {
      write.onError(problem.id, 'Geçici çözüm kaydedilemedi', e);
    },
  });
  const publish = useMutation({
    mutationFn: () => problemsApi.publishKnownError(problem.id),
    onSuccess: (p) => {
      setConfirm(false);
      write.onSuccess(p, 'Bilinen hata yayımlandı');
    },
    onError: (e) => {
      setConfirm(false);
      write.onError(problem.id, 'Bilinen hata yayımlanamadı', e);
    },
  });
  return (
    <section className="card stack" aria-labelledby="sorun-gecici">
      <h2 id="sorun-gecici" className={styles.sectionHeading}>
        Geçici çözüm ve bilinen hata
      </h2>
      <TextArea
        label="Geçici çözüm (workaround)"
        hint="Kullanıcıların ve hizmet masasının kalıcı çözüm gelene kadar uygulayacağı adımlar."
        rows={4}
        maxLength={20000}
        disabled={closed}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
        }}
      />
      {problem.known_error_article_id && (
        <p className={styles.meta}>
          Bilinen hata makalesi:{' '}
          <Link to={`/bilgi-bankasi/${encodeURIComponent(problem.known_error_article_id)}`}>
            bilgi bankasında aç
          </Link>
        </p>
      )}
      {!closed && (
        <div className={styles.buttons}>
          <Button
            variant="secondary"
            disabled={!dirty}
            loading={save.isPending}
            onClick={() => {
              save.mutate();
            }}
          >
            Geçici çözümü kaydet
          </Button>
          <Button
            disabled={dirty || !(problem.workaround ?? '').trim()}
            onClick={() => {
              setConfirm(true);
            }}
          >
            {problem.known_error_article_id
              ? 'Bilinen hatayı güncelle'
              : 'Bilinen hata olarak yayımla'}
          </Button>
        </div>
      )}
      {!closed && dirty && <p className={styles.meta}>Yayımlamadan önce geçici çözümü kaydedin.</p>}
      <ConfirmDialog
        open={confirm}
        title="Bilinen hata yayımlansın mı?"
        description="Geçici çözüm bilgi bankasında bilinen hata (KEDB) makalesi olarak yayımlanır ve sorun “Bilinen hata” durumuna geçer. Yapay zekâ bu adımı yalnızca insan onayıyla (R2) yapabilir."
        confirmLabel="Yayımla"
        cancelLabel="Vazgeç"
        busy={publish.isPending}
        onConfirm={() => {
          publish.mutate();
        }}
        onCancel={() => {
          setConfirm(false);
        }}
      />
    </section>
  );
}

/** Kalıcı çözüm: taslak RFC önerisi (araç boru hattı) → değişiklik bağlantısı ya da onay bekler */
function Fix({ problem }: { problem: Problem }) {
  const { hasRole } = useAuth();
  const canSeeChanges = hasRole(...ROUTE_ROLES.changes);
  const canOpenApprovals = hasRole(...ROUTE_ROLES.approvals);
  const queryClient = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [plan, setPlan] = useState('');
  const [backout, setBackout] = useState('');
  const [test, setTest] = useState('');
  const [outcome, setOutcome] = useState<AgentOutcome | null>(null);
  const closed = problem.state === 'closed';

  const propose = useMutation({
    mutationFn: () =>
      problemsApi.proposeFix(problem.id, {
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(plan.trim() ? { implementation_plan: plan.trim() } : {}),
        ...(backout.trim() ? { backout_plan: backout.trim() } : {}),
        ...(test.trim() ? { test_plan: test.trim() } : {}),
      }),
    onSuccess: (res) => {
      setOpen(false);
      const o = agentOutcome(res);
      setOutcome(o);
      void queryClient.invalidateQueries({ queryKey: queryKeys.problems.detail(problem.id) });
      if (o.kind === 'error') {
        toast.show({ title: 'Kalıcı çözüm önerilemedi', description: o.message, tone: 'error' });
      }
    },
    onError: (e) => {
      toast.show({
        title: 'Kalıcı çözüm önerilemedi',
        description: errorMessage(e),
        tone: 'error',
      });
    },
  });

  return (
    <section className="card stack" aria-labelledby="sorun-cozum">
      <h2 id="sorun-cozum" className={styles.sectionHeading}>
        Kalıcı çözüm
      </h2>
      {problem.change_ids.length === 0 ? (
        <p className={styles.meta}>Bağlı değişiklik (RFC) yok.</p>
      ) : (
        <ul className={styles.readRca} aria-label="Bağlı değişiklikler">
          {problem.change_ids.map((id, i) => (
            <li key={id}>
              {canSeeChanges ? (
                <Link to={`/degisiklikler/${encodeURIComponent(id)}`}>
                  Değişiklik {String(i + 1)}
                </Link>
              ) : (
                `Değişiklik ${String(i + 1)} (görüntüleme yetkiniz yok)`
              )}
            </li>
          ))}
        </ul>
      )}
      {outcome?.kind === 'executed' && (
        <p className={styles.notice} role="status">
          Taslak değişiklik açıldı
          {outcome.changeId && canSeeChanges ? (
            <>
              :{' '}
              <Link to={`/degisiklikler/${encodeURIComponent(outcome.changeId)}`}>
                {outcome.changeNumber ?? 'değişikliği aç'}
              </Link>
            </>
          ) : (
            outcome.changeNumber && `: ${outcome.changeNumber}`
          )}
          . Değişiklik normal CAB sürecinden geçer.
        </p>
      )}
      {outcome?.kind === 'pending_approval' && (
        <p className={styles.notice} role="status">
          Öneri insan onayına gönderildi: {outcome.message}{' '}
          {canOpenApprovals && outcome.actionRequestId && (
            <Link to={`/onaylar?sekme=eylem&istek=${encodeURIComponent(outcome.actionRequestId)}`}>
              Eylem onayını aç
            </Link>
          )}
        </p>
      )}
      {!closed && (
        <div>
          <Button
            variant="secondary"
            onClick={() => {
              setOpen(true);
            }}
          >
            Kalıcı çözüm öner (taslak RFC)
          </Button>
        </div>
      )}
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        title="Kalıcı çözüm öner"
        description="Yapay zekâ bu sorun için taslak bir değişiklik (RFC) hazırlar; boş bıraktığınız alanları kök nedenden doldurur. Taslak, CAB sürecinden geçmeden uygulanmaz."
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setOpen(false);
              }}
            >
              Vazgeç
            </Button>
            <Button
              loading={propose.isPending}
              onClick={() => {
                propose.mutate();
              }}
            >
              Taslak RFC öner
            </Button>
          </>
        }
      >
        <div className="stack">
          <TextInput
            label="Değişiklik başlığı (isteğe bağlı)"
            maxLength={300}
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
            }}
          />
          <TextArea
            label="Uygulama planı (isteğe bağlı)"
            rows={3}
            value={plan}
            onChange={(e) => {
              setPlan(e.target.value);
            }}
          />
          <TextArea
            label="Geri dönüş planı (isteğe bağlı)"
            rows={2}
            value={backout}
            onChange={(e) => {
              setBackout(e.target.value);
            }}
          />
          <TextArea
            label="Test planı (isteğe bağlı)"
            rows={2}
            value={test}
            onChange={(e) => {
              setTest(e.target.value);
            }}
          />
        </div>
      </Dialog>
    </section>
  );
}

function Transition({ problem }: { problem: Problem }) {
  const targets = PROBLEM_TRANSITIONS[problem.state];
  const [to, setTo] = useState<ProblemState | ''>('');
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const write = useProblemWrite();
  const move = useMutation({
    mutationFn: (target: ProblemState) =>
      problemsApi.transition(problem.id, problem.version, target, note.trim() || undefined),
    onSuccess: (p) => {
      setTo('');
      setNote('');
      write.onSuccess(p, `Durum: ${PROBLEM_STATE_LABELS[p.state]}`);
    },
    onError: (e) => {
      write.onError(problem.id, 'Durum değiştirilemedi', e);
    },
  });
  if (targets.length === 0) return null;
  const needsNote = to === 'closed' && problem.state !== 'resolved';
  return (
    <section className="card stack" aria-labelledby="sorun-durum">
      <h2 id="sorun-durum" className={styles.sectionHeading}>
        Durum
      </h2>
      <Select
        label="Yeni durum"
        placeholder="Seçiniz"
        options={targets.map((t) => ({ value: t, label: PROBLEM_STATE_LABELS[t] }))}
        value={to}
        onChange={(e) => {
          const v = targets.find((t) => t === e.target.value);
          setTo(v ?? '');
          setError(null);
        }}
      />
      <TextArea
        label="Not"
        hint={
          needsNote
            ? 'Çözülmemiş sorun yalnızca kapanış notuyla (ör. yinelenen kayıt) kapanır.'
            : 'İsteğe bağlı'
        }
        rows={2}
        maxLength={4000}
        value={note}
        error={error}
        onChange={(e) => {
          setNote(e.target.value);
          setError(null);
        }}
      />
      <div>
        <Button
          variant="secondary"
          disabled={!to}
          loading={move.isPending}
          onClick={() => {
            if (!to) return;
            if (needsNote && !note.trim()) {
              setError('Kapanış notu zorunludur.');
              return;
            }
            move.mutate(to);
          }}
        >
          Durumu değiştir
        </Button>
      </div>
    </section>
  );
}
