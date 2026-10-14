import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  catalogApi,
  errorMessage,
  isApiError,
  queryKeys,
  requestsApi,
  type CatalogItem,
  type ServiceRequest,
} from '@/api';
import { ROUTE_ROLES } from '@/app/navigation';
import { useAuth } from '@/auth/useAuth';
import {
  Badge,
  Button,
  ConfirmDialog,
  ErrorState,
  Spinner,
  TextArea,
  buttonClassName,
  useToast,
} from '@/components';

import { formatDateTime } from '@/lib/format';
import { BUSINESS_APPROVAL_LABELS, CATALOG_APPROVAL_LABELS, sameIdentity } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { enumOptionLabel, parseFormSchema } from '@/features/catalog/formSchema';
import { BusinessDecision } from './BusinessDecision';
import { RequestStateBadge, TouchlessBadge } from './RequestBadges';
import { StepTimeline } from './StepTimeline';
import styles from './Requests.module.css';

const OPEN = new Set(['submitted', 'awaiting_approval', 'fulfilling']);
const CANCELLABLE = new Set(['submitted', 'awaiting_approval', 'fulfilling', 'failed']);
/** Açık talep karşılanırken durum periyodik tazelenir */
const REFRESH_MS = 5000;

/** /taleplerim/:id — hizmet talebi ayrıntısı (sahibi: requests). GET /requests/{id} */
export default function RequestDetailPage() {
  const { id = '' } = useParams();
  const query = useQuery({
    queryKey: queryKeys.requests.detail(id),
    queryFn: ({ signal }) => requestsApi.get(id, signal),
    refetchInterval: (q) => (q.state.data && OPEN.has(q.state.data.state) ? REFRESH_MS : false),
  });
  usePageTitle(query.data ? `${query.data.number} · ${query.data.item_name}` : 'Talep');

  if (query.isPending) {
    return (
      <div className="page">
        <Spinner block label="Talep yükleniyor…" showLabel />
      </div>
    );
  }
  if (query.isError) {
    const notFound = isApiError(query.error) && query.error.isNotFound;
    return (
      <div className="page">
        <h1>Talep</h1>
        <ErrorState
          error={query.error}
          title={notFound ? 'Talep bulunamadı' : 'Talep yüklenemedi'}
          {...(notFound ? {} : { onRetry: () => void query.refetch() })}
          action={
            <Link to="/taleplerim?sekme=hizmet" className={buttonClassName('secondary')}>
              Taleplerime dön
            </Link>
          }
        />
      </div>
    );
  }
  return <RequestDetail request={query.data} />;
}

function RequestDetail({ request }: { request: ServiceRequest }) {
  const { user, hasRole } = useAuth();
  const canOpenApprovals = hasRole(...ROUTE_ROLES.approvals);
  const isOwner =
    sameIdentity(user?.username, request.requester) ||
    sameIdentity(user?.username, request.opened_by);
  const canCancel = CANCELLABLE.has(request.state) && (isOwner || hasRole('agent'));

  return (
    <div className="page">
      <header className="page-header">
        <div className="stack" style={{ gap: 'var(--space-2)' }}>
          <p className="muted" style={{ margin: 0 }}>
            <Link to="/taleplerim?sekme=hizmet">Taleplerim</Link> · Hizmet talebi
          </p>
          <h1>
            {request.number} · {request.item_name}
          </h1>
          <div className={styles.badges}>
            <RequestStateBadge state={request.state} />
            <TouchlessBadge request={request} />
            {request.standard && <Badge tone="info">Standart talep</Badge>}
          </div>
        </div>
        {canCancel && <CancelButton request={request} />}
      </header>

      <Outcome request={request} />

      <div className={styles.layout}>
        <section className="card" aria-labelledby="talep-adimlar">
          <h2 id="talep-adimlar" className={styles.sectionHeading}>
            Karşılama adımları
          </h2>
          <StepTimeline request={request} canOpenApprovals={canOpenApprovals} />
        </section>

        <div className="stack">
          <section className="card" aria-labelledby="talep-ozet">
            <h2 id="talep-ozet" className={styles.sectionHeading}>
              Özet
            </h2>
            <dl className={styles.facts}>
              <dt>Talep eden</dt>
              <dd>{request.requester}</dd>
              {!sameIdentity(request.opened_by, request.requester) && (
                <>
                  <dt>Açan</dt>
                  <dd>{request.opened_by}</dd>
                </>
              )}
              <dt>Açılış</dt>
              <dd>{formatDateTime(request.created_at)}</dd>
              {request.fulfilled_at && (
                <>
                  <dt>Karşılandı</dt>
                  <dd>{formatDateTime(request.fulfilled_at)}</dd>
                </>
              )}
              {request.closed_at && !request.fulfilled_at && (
                <>
                  <dt>Kapandı</dt>
                  <dd>{formatDateTime(request.closed_at)}</dd>
                </>
              )}
            </dl>
          </section>

          <section className="card" aria-labelledby="talep-onay">
            <h2 id="talep-onay" className={styles.sectionHeading}>
              İş onayı
            </h2>
            <dl className={styles.facts}>
              <dt>Politika</dt>
              <dd>{CATALOG_APPROVAL_LABELS[request.approval_policy]}</dd>
              {request.approval_policy !== 'none' && (
                <>
                  <dt>Durum</dt>
                  <dd>{BUSINESS_APPROVAL_LABELS[request.approval_state]}</dd>
                  {request.approver && (
                    <>
                      <dt>Onaylayan</dt>
                      <dd>{request.approved_by ?? request.approver}</dd>
                    </>
                  )}
                  {request.approved_at && (
                    <>
                      <dt>Karar</dt>
                      <dd>{formatDateTime(request.approved_at)}</dd>
                    </>
                  )}
                  {request.approval_comment && (
                    <>
                      <dt>Yorum</dt>
                      <dd className="prewrap">{request.approval_comment}</dd>
                    </>
                  )}
                </>
              )}
            </dl>
            {request.can_decide && <BusinessDecision request={request} />}
          </section>

          <FormValues request={request} />
        </div>
      </div>
    </div>
  );
}

function Outcome({ request }: { request: ServiceRequest }) {
  if (request.state === 'fulfilled' && request.touchless === true) {
    return (
      <p className={styles.notice} role="status">
        Bu talep <strong>insansız çözüldü</strong>: hiçbir insan onayı ya da müdahalesi olmadan
        otomasyonla karşılandı.
      </p>
    );
  }
  if (request.state === 'fulfilled') {
    return (
      <p className={styles.notice} role="status">
        Talep karşılandı.
        {request.touchless_reason && (
          <>
            {' '}
            İnsan katkısı: <span className="prewrap">{request.touchless_reason}</span>
          </>
        )}
      </p>
    );
  }
  if (request.state === 'awaiting_approval' && request.can_decide) {
    return (
      <p className={`${styles.notice} ${styles.noticeWarn}`} role="status">
        Bu talep <strong>sizin iş onayınızı bekliyor</strong>. Onaylarsanız karşılama hemen başlar;
        riskli adımlar ayrıca BT onayından geçer.
      </p>
    );
  }
  if (request.state === 'awaiting_approval') {
    return (
      <p className={`${styles.notice} ${styles.noticeWarn}`} role="status">
        Talep iş onayı bekliyor
        {request.approver ? ` (${request.approver})` : ''}. Onaylanınca karşılama başlar.
      </p>
    );
  }
  if (request.state === 'fulfilling' && request.waiting_action_id) {
    return (
      <p className={`${styles.notice} ${styles.noticeWarn}`} role="status">
        Bir adım BT onayı bekliyor; onaylanınca karşılama kaldığı yerden sürer.
      </p>
    );
  }
  if ((request.state === 'failed' || request.state === 'rejected') && request.error) {
    return (
      <p className={`${styles.notice} ${styles.noticeDanger}`} role="alert">
        {request.error}
      </p>
    );
  }
  return null;
}

function FormValues({ request }: { request: ServiceRequest }) {
  const item = useQuery({
    queryKey: queryKeys.catalog.detail(request.item_key),
    queryFn: ({ signal }) => catalogApi.get(request.item_key, signal),
    staleTime: 5 * 60_000,
  });
  const rows = useMemo(() => formRows(item.data, request.form_values), [item.data, request]);
  // Alan başlıkları katalogdan gelir; yüklenmeden ham alan adı/değer göstermeyiz
  if (item.isPending || rows.length === 0) return null;
  return (
    <section className="card" aria-labelledby="talep-form">
      <h2 id="talep-form" className={styles.sectionHeading}>
        Form bilgileri
      </h2>
      <dl className={styles.facts}>
        {rows.map(([name, label, value]) => (
          <div key={name} style={{ display: 'contents' }}>
            <dt>{label}</dt>
            <dd className="prewrap">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function formRows(item: CatalogItem | undefined, values: Record<string, unknown>) {
  const fields = item ? parseFormSchema(item.form_schema).fields : [];
  const titled = new Map(fields.map((f) => [f.name, f]));
  return Object.entries(values)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([name, v]) => {
      const field = titled.get(name);
      let shown: string;
      if (typeof v === 'boolean') shown = v ? 'Evet' : 'Hayır';
      else if (typeof v === 'string') shown = field?.kind === 'enum' ? enumOptionLabel(v) : v;
      else shown = JSON.stringify(v);
      return [name, field?.title ?? 'Diğer alan', shown] as const;
    });
}

function CancelButton({ request }: { request: ServiceRequest }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const queryClient = useQueryClient();
  const toast = useToast();
  const cancel = useMutation({
    mutationFn: () => requestsApi.cancel(request.id, request.version, note.trim()),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKeys.requests.detail(updated.id), updated);
      void queryClient.invalidateQueries({ queryKey: queryKeys.requests.lists });
      setOpen(false);
      toast.show({ title: `${updated.number} iptal edildi`, tone: 'success' });
    },
    onError: (e) => {
      if (isApiError(e) && e.isConflict) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.requests.detail(request.id) });
      }
      toast.show({ title: 'Talep iptal edilemedi', description: errorMessage(e), tone: 'error' });
    },
  });
  return (
    <>
      <Button
        variant="secondary"
        onClick={() => {
          setOpen(true);
        }}
      >
        Talebi iptal et
      </Button>
      <ConfirmDialog
        open={open}
        title={`${request.number} iptal edilsin mi?`}
        description="Tamamlanmış adımlar geri alınmaz; bekleyen onaylar ve kalan adımlar durdurulur."
        confirmLabel="İptal et"
        cancelLabel="Vazgeç"
        tone="danger"
        busy={cancel.isPending}
        onConfirm={() => {
          cancel.mutate();
        }}
        onCancel={() => {
          setOpen(false);
        }}
      >
        <TextArea
          label="Not (isteğe bağlı)"
          rows={3}
          maxLength={2000}
          value={note}
          onChange={(e) => {
            setNote(e.target.value);
          }}
        />
      </ConfirmDialog>
    </>
  );
}
