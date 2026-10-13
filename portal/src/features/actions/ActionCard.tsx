import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useId, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { actionsApi, errorMessage, isApiError, queryKeys, type ActionRequest } from '@/api';
import { ROUTE_ROLES } from '@/app/navigation';
import { useAuth } from '@/auth/useAuth';
import { Badge, Button, ConfirmDialog, TextArea, useToast, type Tone } from '@/components';
import { formatDateTime, formatPercent, formatRelative } from '@/lib/format';
import {
  ACTION_STATE_LABELS,
  APPROVAL_DECISION_LABELS,
  RISK_CLASS_DESCRIPTIONS,
  RISK_CLASS_LABELS,
  actorLabel,
} from '@/lib/labels';
import {
  BLOCK_MESSAGES,
  approvalCount,
  blockReason,
  isExpired,
  isFinalApproval,
  progressLabel,
} from './rules';
import styles from './Actions.module.css';

const STATE_TONES: Record<ActionRequest['state'], Tone> = {
  pending: 'warning',
  approved: 'info',
  rejected: 'danger',
  executed: 'success',
  failed: 'danger',
  expired: 'neutral',
};

function show(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'Evet' : 'Hayır';
  if (typeof value === 'number') return String(value);
  return JSON.stringify(value, null, 2);
}

interface Props {
  action: ActionRequest;
  highlight?: boolean;
}

/** Tek eylem isteği: risk, araç, maskeli argümanlar, gerekçe, bağlı kayıtlar, onaylar, karar */
export function ActionCard({ action, highlight = false }: Props) {
  const headingId = useId();
  const expired = isExpired(action);
  const stateLabel = expired ? ACTION_STATE_LABELS.expired : ACTION_STATE_LABELS[action.state];
  const approvals = action.approvals ?? [];

  return (
    <article
      className={styles.card}
      aria-labelledby={headingId}
      data-risk={action.risk_class}
      data-highlight={highlight ? 'true' : undefined}
      data-action-id={action.id}
    >
      <div className={styles.head}>
        <h3 id={headingId} className={styles.title}>
          <span className={styles.number}>{action.number}</span>{' '}
          {action.tool_description ?? action.tool}
        </h3>
        <div className={styles.badges}>
          <Badge
            tone={action.risk_class === 'R3' ? 'danger' : 'warning'}
            strong
            title={RISK_CLASS_DESCRIPTIONS[action.risk_class]}
          >
            {RISK_CLASS_LABELS[action.risk_class]}
          </Badge>
          <Badge tone={expired ? 'neutral' : STATE_TONES[action.state]} dot>
            {stateLabel}
          </Badge>
        </div>
      </div>

      <p className={styles.meta}>
        Araç: <code className={styles.tool}>{action.tool}</code> · İsteyen:{' '}
        {actorLabel(action.requested_by)}
        {action.confidence !== null && ` · Güven ${formatPercent(action.confidence)}`} ·{' '}
        {formatRelative(action.created_at)}
      </p>
      <p className={styles.meta}>{RISK_CLASS_DESCRIPTIONS[action.risk_class]}</p>

      {action.reason && (
        <div>
          <h4 className={styles.subheading}>Gerekçe</h4>
          <p className="prewrap" style={{ margin: 0 }}>
            {action.reason}
          </p>
        </div>
      )}

      <div className={styles.grid}>
        <div>
          <h4 className={styles.subheading}>Argümanlar (maskeli)</h4>
          {Object.keys(action.args_display).length === 0 ? (
            <p className={styles.meta}>Argüman yok.</p>
          ) : (
            <dl className={styles.args}>
              {Object.entries(action.args_display).map(([k, v]) => (
                <div key={k} style={{ display: 'contents' }}>
                  <dt>{k}</dt>
                  <dd>{show(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
        <div className="stack" style={{ gap: 'var(--space-3)' }}>
          <LinkedRecords action={action} />
          <div>
            <h4 className={styles.subheading}>
              Onaylar <span className="muted">({progressLabel(action)})</span>
            </h4>
            {action.risk_class === 'R3' && action.change_id && action.state === 'pending' && (
              <p className={styles.meta}>Bağlı değişiklik CAB onaylıysa tek onay yeterlidir.</p>
            )}
            {approvals.length === 0 ? (
              <p className={styles.meta}>Henüz karar yok.</p>
            ) : (
              <ul className={styles.approvals}>
                {approvals.map((a) => (
                  <li key={a.id}>
                    <strong>{a.approver}</strong>: {APPROVAL_DECISION_LABELS[a.decision]} ·{' '}
                    {formatDateTime(a.created_at)}
                    {a.comment && <span className="prewrap"> — “{a.comment}”</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      <p className={styles.meta}>
        {expired
          ? `Süresi doldu (${formatDateTime(action.expires_at)}); bu istek artık onaylanamaz.`
          : action.state === 'pending'
            ? `Son karar zamanı: ${formatDateTime(action.expires_at)}`
            : null}
      </p>

      {action.state === 'executed' && (
        <p className={styles.result}>
          {actorLabel(action.executed_by)} kimliğiyle yürütüldü ·{' '}
          {formatDateTime(action.executed_at)}
        </p>
      )}
      {action.error && <p className={styles.error}>{action.error}</p>}

      {action.state === 'pending' && <Decision action={action} expired={expired} />}
    </article>
  );
}

function LinkedRecords({ action }: { action: ActionRequest }) {
  const { hasRole } = useAuth();
  const problemId =
    typeof action.args_display.problem_id === 'string' ? action.args_display.problem_id : null;
  const items: { key: string; node: ReactNode }[] = [];
  if (action.incident_id) {
    items.push({
      key: 'incident',
      node: hasRole(...ROUTE_ROLES.incidents) ? (
        <Link to={`/kayitlar/${encodeURIComponent(action.incident_id)}`}>Olay kaydı</Link>
      ) : (
        'Olay kaydı'
      ),
    });
  }
  if (action.change_id) {
    items.push({
      key: 'change',
      node: hasRole(...ROUTE_ROLES.changes) ? (
        <Link to={`/degisiklikler/${encodeURIComponent(action.change_id)}`}>Değişiklik kaydı</Link>
      ) : (
        'Değişiklik kaydı (görüntüleme yetkiniz yok)'
      ),
    });
  }
  if (action.request_id) {
    items.push({
      key: 'request',
      node: <Link to={`/taleplerim/${encodeURIComponent(action.request_id)}`}>Hizmet talebi</Link>,
    });
  }
  if (problemId) {
    items.push({
      key: 'problem',
      node: hasRole(...ROUTE_ROLES.problems) ? (
        <Link to={`/problemler/${encodeURIComponent(problemId)}`}>Sorun kaydı</Link>
      ) : (
        'Sorun kaydı'
      ),
    });
  }
  return (
    <div>
      <h4 className={styles.subheading}>Bağlı kayıtlar</h4>
      {items.length === 0 ? (
        <p className={styles.meta}>Bağlı kayıt yok.</p>
      ) : (
        <ul className={styles.links}>
          {items.map((i) => (
            <li key={i.key}>{i.node}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Decision({ action, expired }: { action: ActionRequest; expired: boolean }) {
  const { user, hasRole } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const commentId = useId();
  const blockedId = useId();
  const [comment, setComment] = useState('');
  const [commentError, setCommentError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const reason = expired
    ? 'expired'
    : blockReason(action, { username: user?.username ?? '', hasRole });

  const onSettled = (updated: ActionRequest | undefined) => {
    if (updated) queryClient.setQueryData(queryKeys.actions.detail(updated.id), updated);
    void queryClient.invalidateQueries({ queryKey: queryKeys.actions.all });
    void queryClient.invalidateQueries({ queryKey: queryKeys.requests.all });
  };
  const onError = (e: unknown) => {
    onSettled(undefined);
    toast.show({
      title: isApiError(e) && e.isConflict ? 'İstek değişti' : 'Karar kaydedilemedi',
      description: errorMessage(e),
      tone: 'error',
    });
  };

  const approve = useMutation({
    mutationFn: () => actionsApi.approve(action.id, comment.trim()),
    onSuccess: (updated) => {
      setConfirm(false);
      onSettled(updated);
      if (updated.state === 'executed') {
        toast.show({ title: `${updated.number} onaylandı ve yürütüldü`, tone: 'success' });
      } else if (updated.state === 'failed') {
        toast.show({
          title: `${updated.number} onaylandı ancak yürütme başarısız`,
          ...(updated.error ? { description: updated.error } : {}),
          tone: 'error',
        });
      } else {
        toast.show({
          title: `Onayınız kaydedildi (${progressLabel(updated)})`,
          description: 'Eylem, gereken onaylar tamamlanınca yürütülecek.',
          tone: 'info',
        });
      }
    },
    onError: (e) => {
      setConfirm(false);
      onError(e);
    },
  });
  const reject = useMutation({
    mutationFn: () => actionsApi.reject(action.id, comment.trim()),
    onSuccess: (updated) => {
      onSettled(updated);
      toast.show({ title: `${updated.number} reddedildi`, tone: 'success' });
    },
    onError,
  });

  const busy = approve.isPending || reject.isPending;
  const blocked = reason !== null;
  const final = isFinalApproval(action);

  return (
    <div className={styles.decision}>
      {blocked && reason !== 'not_pending' && (
        <p id={blockedId} className={styles.blocked} data-blocked={reason}>
          {BLOCK_MESSAGES[reason]}
        </p>
      )}
      {!blocked && (
        <TextArea
          id={commentId}
          label="Yorum"
          hint="Reddederken gerekçe zorunludur; onay yorumu isteğe bağlıdır."
          rows={2}
          maxLength={2000}
          value={comment}
          error={commentError}
          onChange={(e) => {
            setComment(e.target.value);
            setCommentError(null);
          }}
        />
      )}
      <div className={styles.buttons}>
        <Button
          disabled={blocked || busy}
          loading={approve.isPending}
          aria-describedby={blocked ? blockedId : undefined}
          onClick={() => {
            setConfirm(true);
          }}
        >
          Onayla
        </Button>
        <Button
          variant="danger"
          disabled={blocked || busy}
          loading={reject.isPending}
          aria-describedby={blocked ? blockedId : undefined}
          onClick={() => {
            if (!comment.trim()) {
              setCommentError('Reddetmek için gerekçe yazın.');
              return;
            }
            reject.mutate();
          }}
        >
          Reddet
        </Button>
      </div>
      <ConfirmDialog
        open={confirm}
        title={`${action.number} onaylansın mı?`}
        description={
          final
            ? `Onayınızla “${action.tool_description ?? action.tool}” sizin kimliğinizle hemen yürütülecek.`
            : `Onayınız kaydedilecek (${String(approvalCount(action) + 1)}/${String(action.required_approvals)}); eylem kalan onaydan sonra yürütülür.`
        }
        confirmLabel={final ? 'Onayla ve yürüt' : 'Onayımı kaydet'}
        cancelLabel="Vazgeç"
        busy={approve.isPending}
        onConfirm={() => {
          approve.mutate();
        }}
        onCancel={() => {
          setConfirm(false);
        }}
      />
    </div>
  );
}
