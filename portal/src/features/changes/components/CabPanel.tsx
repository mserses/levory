import { useId, useState, type FormEvent } from 'react';
import { isApiError } from '@/api/errors';
import type { ApprovalDecision, Change, ChangeApprovalList } from '@/api/types';
import {
  Badge,
  Button,
  ConfirmDialog,
  ErrorState,
  Spinner,
  TextArea,
  useToast,
} from '@/components';
import { formatDateTime } from '@/lib/format';
import { APPROVAL_DECISION_LABELS, ROLE_LABELS } from '@/lib/labels';
import styles from '../Changes.module.css';
import { useVote } from '../hooks';
import { voteBlockReason, type ChangeActor } from '../rules';

export interface CabPanelProps {
  change: Change;
  actor: ChangeActor;
  /** Kullanıcının cab/change_manager rolü var mı (oy formu gösterilir) */
  canVote: boolean;
  approvals: ChangeApprovalList | undefined;
  approvalsError: unknown;
  approvalsPending: boolean;
}

/** CAB/ECAB onayları: liste, durum özeti ve oy formu (görevler ayrılığı gerekçeli) */
export function CabPanel({
  change,
  actor,
  canVote,
  approvals,
  approvalsError,
  approvalsPending,
}: CabPanelProps) {
  const headingId = useId();
  const standard = change.change_type === 'standard';
  return (
    <section className="card stack" aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.sectionTitle}>
        CAB onayları
      </h2>
      {standard ? (
        <p className="muted" style={{ margin: 0 }}>
          Standart değişiklik önceden onaylıdır; CAB oyu gerekmez. Uygunluk zamanlama ve uygulama
          anında yeniden denetlenir.
        </p>
      ) : approvalsError ? (
        <ErrorState error={approvalsError} />
      ) : approvalsPending || !approvals ? (
        <Spinner label="Onaylar yükleniyor…" />
      ) : (
        <>
          <ApprovalStatusLine change={change} approvals={approvals} />
          {approvals.items.length > 0 ? (
            <ul className={styles.approvals}>
              {approvals.items.map((a) => (
                <li key={a.id}>
                  <div className="row">
                    <strong>{a.approver}</strong>
                    <Badge tone={a.decision === 'approve' ? 'success' : 'danger'} dot>
                      {APPROVAL_DECISION_LABELS[a.decision]}
                    </Badge>
                    <span className="muted">
                      {a.approver_roles.map((r) => ROLE_LABELS[r] ?? r).join(', ')} ·{' '}
                      {formatDateTime(a.created_at)}
                    </span>
                  </div>
                  {a.comment && (
                    <p className="prewrap" style={{ margin: 0 }}>
                      {a.comment}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              Henüz oy verilmedi.
            </p>
          )}
          {canVote && (
            <VoteForm
              change={change}
              blockReason={voteBlockReason(change, approvals.items, actor)}
            />
          )}
        </>
      )}
    </section>
  );
}

function ApprovalStatusLine({
  change,
  approvals,
}: {
  change: Change;
  approvals: ChangeApprovalList;
}) {
  const { status } = approvals;
  return (
    <div className="stack" style={{ gap: 'var(--space-1)' }}>
      <div className="row">
        <span>{`Onay ${status.approvals}/${status.required}`}</span>
        {status.rejections > 0 && <span>{`· Red ${status.rejections}`}</span>}
        <Badge tone={status.satisfied ? 'success' : 'warning'} dot>
          {status.satisfied ? 'Yetkilendirme yeterli' : 'Yetkilendirme eksik'}
        </Badge>
      </div>
      <p className="muted" style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
        {change.change_type === 'emergency'
          ? 'Acil değişiklikte (ECAB) yalnızca değişiklik yöneticisi onayları sayılır.'
          : 'Tek bir ret değişikliği reddeder. Yeterli onay zamanlamayı kendiliğinden yapmaz; değişiklik yöneticisi zamanlar.'}
      </p>
    </div>
  );
}

function VoteForm({ change, blockReason }: { change: Change; blockReason: string | null }) {
  const toast = useToast();
  const vote = useVote(change.id);
  const [decision, setDecision] = useState<ApprovalDecision | ''>('');
  const [comment, setComment] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [confirmReject, setConfirmReject] = useState(false);
  const reasonId = useId();
  const disabled = blockReason !== null;

  const send = (d: ApprovalDecision) => {
    vote.mutate(
      { decision: d, comment: comment.trim() },
      {
        onSuccess: () => {
          setConfirmReject(false);
          toast.show({
            title:
              d === 'approve' ? 'Onayınız kaydedildi' : 'Ret kaydedildi; değişiklik reddedildi',
            tone: d === 'approve' ? 'success' : 'info',
          });
          setDecision('');
          setComment('');
        },
        onError: (e) => {
          setConfirmReject(false);
          toast.show({
            title: 'Oy kaydedilemedi',
            description: isApiError(e) ? e.detail : undefined,
            tone: 'error',
          });
        },
      },
    );
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!decision) {
      setError('Bir karar seçin.');
      return;
    }
    if (decision === 'reject') {
      if (!comment.trim()) {
        setError('Ret için gerekçe yazın.');
        return;
      }
      setConfirmReject(true);
      return;
    }
    send('approve');
  };

  return (
    <form
      className="stack"
      aria-label="CAB oyu"
      noValidate
      onSubmit={submit}
      aria-describedby={disabled ? reasonId : undefined}
    >
      <fieldset className={styles.radioRow} disabled={disabled}>
        <legend>Kararınız</legend>
        <label>
          <input
            type="radio"
            name="cab-decision"
            value="approve"
            checked={decision === 'approve'}
            onChange={() => {
              setDecision('approve');
            }}
          />
          Onayla
        </label>
        <label>
          <input
            type="radio"
            name="cab-decision"
            value="reject"
            checked={decision === 'reject'}
            onChange={() => {
              setDecision('reject');
            }}
          />
          Reddet
        </label>
      </fieldset>
      <TextArea
        label="Yorum"
        hint="Ret için zorunlu; denetim kaydına yazılır."
        value={comment}
        maxLength={2000}
        disabled={disabled}
        error={error}
        onChange={(e) => {
          setComment(e.target.value);
        }}
      />
      {disabled && (
        <p id={reasonId} className="muted" style={{ margin: 0 }}>
          {blockReason}
        </p>
      )}
      <div className="row">
        <Button type="submit" disabled={disabled} loading={vote.isPending && !confirmReject}>
          Oyu gönder
        </Button>
      </div>
      <ConfirmDialog
        open={confirmReject}
        title="Değişiklik reddedilsin mi?"
        description="Tek bir ret değişikliği “Reddedildi” durumuna getirir ve geri alınamaz."
        confirmLabel="Reddet"
        tone="danger"
        busy={vote.isPending}
        onConfirm={() => {
          send('reject');
        }}
        onCancel={() => {
          setConfirmReject(false);
        }}
      />
    </form>
  );
}
