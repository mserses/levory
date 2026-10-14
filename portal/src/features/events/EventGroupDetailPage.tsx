import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  errorMessage,
  eventsApi,
  isApiError,
  queryKeys,
  type AgentResult,
  type EventGroupDetail,
} from '@/api';
import { ROUTE_ROLES } from '@/app/navigation';
import { useAuth } from '@/auth/useAuth';
import {
  Badge,
  Button,
  Dialog,
  ErrorState,
  PriorityBadge,
  Select,
  Spinner,
  TextArea,
  buttonClassName,
  useToast,
} from '@/components';
import { formatDateTime, formatRelative } from '@/lib/format';
import { EVENT_STATUS_LABELS, EVENT_SUPPRESSED_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { isPriority } from '@/features/kpi/model';
import { GroupStatus, LinkedIncident, SeverityBadge } from './GroupStatus';
import { SILENCE_OPTIONS, isSilenced, silenceLabel } from './model';
import styles from './Events.module.css';

/** /olaylar/:id — uyarı grubu bağıntısı ve eylemleri (sahibi: events). GET /events/groups/{id} */
export default function EventGroupDetailPage() {
  const { id = '' } = useParams();
  const query = useQuery({
    queryKey: queryKeys.events.group(id),
    queryFn: ({ signal }) => eventsApi.getGroup(id, signal),
  });
  usePageTitle(query.data ? `${query.data.group.number} · ${query.data.group.title}` : 'Olay');

  if (query.isPending) {
    return (
      <div className="page">
        <Spinner block label="Uyarı grubu yükleniyor…" showLabel />
      </div>
    );
  }
  if (query.isError) {
    const notFound = isApiError(query.error) && query.error.isNotFound;
    return (
      <div className="page">
        <h1>Uyarı grubu</h1>
        <ErrorState
          error={query.error}
          title={notFound ? 'Uyarı grubu bulunamadı' : 'Uyarı grubu yüklenemedi'}
          {...(notFound ? {} : { onRetry: () => void query.refetch() })}
          action={
            <Link to="/olaylar" className={buttonClassName('secondary')}>
              Olaylara dön
            </Link>
          }
        />
      </div>
    );
  }
  return <GroupDetail detail={query.data} />;
}

interface PendingSilence {
  actionRequestId: string | null;
  minutes: number;
}

function GroupDetail({ detail }: { detail: EventGroupDetail }) {
  const { group } = detail;
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [silenceOpen, setSilenceOpen] = useState(false);
  // R2 isteği bu oturumda açıldı; onaylanınca grup `silenced_until` ile döner
  const [pendingSilence, setPendingSilence] = useState<PendingSilence | null>(null);
  const open = group.state === 'open';
  const canApprove = hasRole(...ROUTE_ROLES.approvals);

  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.events.all });
  const outcomeError = (r: AgentResult) =>
    (typeof r.error?.message === 'string' ? r.error.message : '') ||
    r.message ||
    'İşlem tamamlanamadı.';

  const ack = useMutation({
    mutationFn: () => eventsApi.acknowledge(group.id),
    onSuccess: (r) => {
      if (r.status === 'executed') toast.show({ title: 'Grup üstlenildi', tone: 'success' });
      else if (r.status === 'pending_approval') {
        toast.show({ title: 'Üstlenme onay bekliyor', description: r.message, tone: 'info' });
      } else toast.show({ title: 'Üstlenilemedi', description: outcomeError(r), tone: 'error' });
      void refresh();
    },
    onError: (e) => {
      toast.show({ title: 'Üstlenilemedi', description: errorMessage(e), tone: 'error' });
    },
  });

  return (
    <div className="page">
      <header className="page-header">
        <div className="stack" style={{ gap: 'var(--space-2)' }}>
          <p className="muted" style={{ margin: 0 }}>
            <Link to="/olaylar">Olaylar</Link> · {group.number}
          </p>
          <h1>
            {group.number} · {group.title}
          </h1>
          <div className={styles.badges}>
            <SeverityBadge severity={group.severity} />
          </div>
        </div>
        {open && (
          <div className={styles.actions}>
            {!group.acknowledged_by && (
              <Button
                variant="secondary"
                loading={ack.isPending}
                onClick={() => {
                  ack.mutate();
                }}
              >
                Üstlen
              </Button>
            )}
            <Button
              variant="secondary"
              disabled={pendingSilence !== null || isSilenced(group)}
              onClick={() => {
                setSilenceOpen(true);
              }}
            >
              Sustur…
            </Button>
          </div>
        )}
      </header>

      {pendingSilence && (
        <div className={styles.notice} role="status" aria-label="Susturma onay bekliyor">
          Susturma isteği ({silenceLabel(pendingSilence.minutes)}) insan onayı bekliyor; onaylanana
          kadar grup susturulmaz.{' '}
          {canApprove ? (
            <Link to="/onaylar?sekme=eylem">Eylem onaylarına git</Link>
          ) : (
            'Onaylayıcı rolündeki bir kişi onaylamalıdır.'
          )}
        </div>
      )}
      {detail.pending_action_request_id && (
        <p className={styles.notice}>Olay açma önerisi insan onayı bekliyor.</p>
      )}

      <div className={styles.layout}>
        <div className="stack">
          <section className="card" aria-labelledby="bagintili-uyarilar">
            <h2 id="bagintili-uyarilar" className={styles.sectionHeading}>
              Bağıntılı uyarılar
            </h2>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className="visually-hidden">Bağıntılı uyarılar</caption>
                <thead>
                  <tr>
                    <th scope="col">Uyarı</th>
                    <th scope="col">Önem</th>
                    <th scope="col">Durum</th>
                    <th scope="col">CMDB yolu</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.members.map((m) => (
                    <tr key={m.event_id}>
                      <th scope="row">{m.title}</th>
                      <td>
                        <SeverityBadge severity={m.severity} />
                      </td>
                      <td>
                        {EVENT_STATUS_LABELS[m.status]}
                        {m.suppressed && (
                          <p className={styles.meta}>
                            {EVENT_SUPPRESSED_LABELS[m.suppressed] ?? 'Bastırıldı'}
                          </p>
                        )}
                      </td>
                      <td className={styles.path}>
                        {m.path.length > 0 ? m.path.join(' → ') : (m.ci_name ?? 'Eşlenmedi')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card" aria-labelledby="uyari-gecmisi">
            <h2 id="uyari-gecmisi" className={styles.sectionHeading}>
              Uyarı kayıtları
            </h2>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className="visually-hidden">Uyarı kayıtları</caption>
                <thead>
                  <tr>
                    <th scope="col">Başlık</th>
                    <th scope="col">Kaynak</th>
                    <th scope="col">Başlangıç</th>
                    <th scope="col">Bitiş</th>
                    <th scope="col">Alınma</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.events.map((e) => (
                    <tr key={e.id}>
                      <th scope="row">{e.title}</th>
                      <td>{e.source}</td>
                      <td>{formatDateTime(e.starts_at)}</td>
                      <td>{formatDateTime(e.ends_at)}</td>
                      <td>{e.received_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <section className="card" aria-labelledby="grup-ozet">
          <h2 id="grup-ozet" className={styles.sectionHeading}>
            Özet
          </h2>
          <dl className={styles.facts}>
            <dt>Durum</dt>
            <dd>
              <GroupStatus group={group} />
            </dd>
            <dt>Kök CI</dt>
            <dd>
              {group.root_ci_id ? (
                <Link to={`/cmdb/${encodeURIComponent(group.root_ci_id)}`}>
                  {detail.root_ci_name ?? 'CMDB kaydı'}
                </Link>
              ) : (
                'Eşlenmedi'
              )}
            </dd>
            <dt>Olay kaydı</dt>
            <dd>
              <LinkedIncident group={group} />
            </dd>
            <dt>Önerilen öncelik</dt>
            <dd>
              {isPriority(detail.proposed_priority) ? (
                <PriorityBadge priority={detail.proposed_priority} />
              ) : (
                '—'
              )}
            </dd>
            {isSilenced(group) && (
              <>
                <dt>Susturma</dt>
                <dd>
                  {group.silenced_by ?? '—'} tarafından susturuldu ·{' '}
                  {formatDateTime(group.silenced_until)} kadar
                </dd>
              </>
            )}
            <dt>İlk uyarı</dt>
            <dd>{formatDateTime(group.first_event_at)}</dd>
            <dt>Son uyarı</dt>
            <dd>{formatRelative(group.last_event_at)}</dd>
            <dt>Uyarı sayısı</dt>
            <dd>{group.event_count}</dd>
            {!detail.actionable && open && !group.incident_id && (
              <>
                <dt>Olay açma</dt>
                <dd>
                  <Badge>Eşik altında / bastırılmış</Badge>
                </dd>
              </>
            )}
          </dl>
        </section>
      </div>

      <SilenceDialog
        open={silenceOpen}
        groupId={group.id}
        onClose={() => {
          setSilenceOpen(false);
        }}
        onDone={(result, minutes) => {
          setSilenceOpen(false);
          if (result.status === 'pending_approval') {
            setPendingSilence({ actionRequestId: result.action_request_id ?? null, minutes });
          } else if (result.status === 'executed') {
            toast.show({ title: 'Grup susturuldu', tone: 'success' });
          } else {
            toast.show({
              title: 'Susturulamadı',
              description: outcomeError(result),
              tone: 'error',
            });
          }
          void refresh();
        }}
      />
    </div>
  );
}

function SilenceDialog({
  open,
  groupId,
  onClose,
  onDone,
}: {
  open: boolean;
  groupId: string;
  onClose: () => void;
  onDone: (result: AgentResult, minutes: number) => void;
}) {
  const [minutes, setMinutes] = useState('60');
  const [comment, setComment] = useState('');
  const [error, setError] = useState<string | null>(null);
  const silence = useMutation({
    mutationFn: () =>
      eventsApi.silence(groupId, { minutes: Number(minutes), comment: comment.trim() }),
    onSuccess: (r) => {
      setComment('');
      setError(null);
      onDone(r, Number(minutes));
    },
    onError: (e) => {
      setError(errorMessage(e));
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!comment.trim()) {
      setError('Gerekçe zorunludur.');
      return;
    }
    silence.mutate();
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      dismissible={!silence.isPending}
      title="Uyarı grubunu sustur"
      description="Susturma (R2) her zaman insan onayı ister; onaylanana kadar yeni uyarılar olay açmaya devam eder."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={silence.isPending}>
            Vazgeç
          </Button>
          <Button type="submit" form="silence-form" loading={silence.isPending}>
            Onaya gönder
          </Button>
        </>
      }
    >
      <form id="silence-form" className={styles.form} onSubmit={submit} noValidate>
        <Select
          label="Süre"
          options={SILENCE_OPTIONS}
          value={minutes}
          onChange={(e) => {
            setMinutes(e.target.value);
          }}
        />
        <TextArea
          label="Gerekçe"
          required
          maxLength={500}
          rows={3}
          value={comment}
          error={error ?? undefined}
          onChange={(e) => {
            setComment(e.target.value);
          }}
        />
      </form>
    </Dialog>
  );
}
