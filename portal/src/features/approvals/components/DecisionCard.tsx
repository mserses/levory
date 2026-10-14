import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { decisionsApi, incidentsApi, isApiError, queryKeys } from '@/api';
import type { DecisionInboxItem } from '@/api/types';
import {
  AIDecisionSummary,
  Button,
  ConfirmDialog,
  PriorityBadge,
  StatusBadge,
  buttonClassName,
  useToast,
} from '@/components';
import { formatRelative } from '@/lib/format';
import styles from '../Approvals.module.css';
import {
  STALE_MESSAGE,
  buildDiff,
  displayValue,
  fieldLabel,
  isLikelyStale,
  parseProposal,
  priorityChange,
} from '../proposal';
import { PriorityChangeNotice } from './PriorityChangeNotice';
import { ProposalDiff } from './ProposalDiff';

type ApproveProblem =
  | { kind: 'stale' }
  | { kind: 'forbidden'; message: string }
  | { kind: 'other'; message: string };

interface DecisionCardProps {
  item: DecisionInboxItem;
}

const APPROVABLE = new Set(['needs_approval', 'suggested']);

export function DecisionCard({ item }: DecisionCardProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [problem, setProblem] = useState<ApproveProblem | null>(null);

  const incidentId = item.incident_id;
  const incidentPath = incidentId ? `/kayitlar/${encodeURIComponent(incidentId)}` : null;
  const parsed = parseProposal(item.output);
  const change = priorityChange(item, parsed);
  const staleHint = isLikelyStale(item);
  const approvable = APPROVABLE.has(item.outcome) && incidentId !== null;

  // "Mevcut" sütunu için kaydın güncel hâli (kayıt ekranıyla aynı önbellek anahtarı)
  const incident = useQuery({
    queryKey: queryKeys.incidents.detail(incidentId ?? ''),
    queryFn: ({ signal }) => incidentsApi.get(incidentId ?? '', signal),
    enabled: incidentId !== null,
  });
  const rows = buildDiff(item, parsed, incident.data);
  const currentNote = incident.isPending
    ? incidentId
      ? 'Mevcut değerler yükleniyor…'
      : undefined
    : incident.isError
      ? 'Kaydın mevcut değerleri alınamadı'
      : undefined;

  const approve = useMutation({
    mutationFn: () => decisionsApi.approve(item.id),
    onSuccess: async () => {
      setConfirmOpen(false);
      toast.show({
        tone: 'success',
        title: 'Öneri onaylandı',
        description: `${item.incident_number ?? 'Kayıt'} güncellendi.`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.decisions.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.incidents.all }),
      ]);
    },
    onError: async (error) => {
      setConfirmOpen(false);
      if (isApiError(error) && error.needsRetriage) {
        setProblem({ kind: 'stale' });
      } else if (isApiError(error) && error.isForbidden) {
        setProblem({ kind: 'forbidden', message: error.detail });
      } else if (isApiError(error) && (error.isConflict || error.isNotFound)) {
        // Ör. karar başkasınca onaylanmış: liste yenilenince kart kaybolabilir, bildirim de göster
        setProblem({ kind: 'other', message: error.detail });
        toast.show({ tone: 'warning', title: 'Öneri onaylanamadı', description: error.detail });
      } else {
        toast.show({
          tone: 'error',
          title: 'Öneri onaylanamadı',
          description: isApiError(error) ? error.detail : 'Beklenmeyen bir hata oluştu.',
        });
        return;
      }
      // Kaydın ve listenin güncel hâli
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.decisions.all }),
        incidentId
          ? queryClient.invalidateQueries({ queryKey: queryKeys.incidents.detail(incidentId) })
          : Promise.resolve(),
      ]);
    },
  });

  const title = item.incident_title ?? 'Bağlı kayıt bulunamadı';
  const changes = rows.filter((r) => r.state !== 'same');

  return (
    <li className={styles.item} data-testid="decision-item" data-decision-id={item.id}>
      <article className={styles.itemCard} aria-labelledby={`d-${item.id}`}>
        <header className={styles.itemHead}>
          <div className={styles.itemTitle}>
            <h3 id={`d-${item.id}`} className={styles.itemHeading}>
              {incidentPath ? (
                <Link to={incidentPath}>
                  <span className={styles.number}>{item.incident_number ?? '—'}</span> {title}
                </Link>
              ) : (
                <>
                  <span className={styles.number}>{item.incident_number ?? '—'}</span> {title}
                </>
              )}
            </h3>
            <p className={styles.itemMeta}>
              Öneri {formatRelative(item.created_at)}
              {item.incident_version !== null && ` · Dayandığı sürüm ${item.incident_version}`}
              {item.incident_current_version !== null &&
                ` · Kaydın güncel sürümü ${item.incident_current_version}`}
            </p>
          </div>
          <div className="row">
            {item.incident_priority && <PriorityBadge priority={item.incident_priority} compact />}
            {item.incident_status && <StatusBadge status={item.incident_status} />}
          </div>
        </header>

        {change && <PriorityChangeNotice change={change} />}

        {staleHint && problem?.kind !== 'stale' && (
          <p className={styles.warning} data-testid="stale-hint">
            <strong>Dikkat:</strong> Kayıt bu öneriden sonra değişti (sürüm {item.incident_version}{' '}
            → {item.incident_current_version}). Onay büyük olasılıkla reddedilecek; önce kaydı
            inceleyip yeniden triage başlatın.
          </p>
        )}

        {problem && (
          <div className={styles.problem} role="alert">
            {problem.kind === 'stale' ? (
              <>
                <p className={styles.problemTitle}>{STALE_MESSAGE}</p>
                <p className={styles.noticeText}>
                  Öneri verildikten sonra kayıt başka biri tarafından güncellendi. İnsan verisinin
                  üzerine yazılmaması için bu öneri uygulanmadı. Kaydı açıp güncel hâliyle yeniden
                  triage başlatın; yeni öneri bu listeye düşecek.
                </p>
                {incidentPath && (
                  <Link className={buttonClassName('secondary', 'sm')} to={incidentPath}>
                    Kayda git ve yeniden triage başlat
                  </Link>
                )}
              </>
            ) : problem.kind === 'forbidden' ? (
              <>
                <p className={styles.problemTitle}>Bu öneriyi onaylama yetkiniz yok.</p>
                <p className={styles.noticeText}>
                  {problem.message} Onaylayıcı rolünüz olduğundan eminseniz yöneticinize başvurun.
                </p>
              </>
            ) : (
              <>
                <p className={styles.problemTitle}>Öneri onaylanamadı.</p>
                <p className={styles.noticeText}>{problem.message}</p>
              </>
            )}
          </div>
        )}

        <ProposalDiff rows={rows} caption="Önerilen değişiklikler" currentNote={currentNote} />

        {parsed.policyReason && (
          <p className={styles.policy}>
            <span className={styles.policyLabel}>Politika: </span>
            <span className="prewrap">{parsed.policyReason}</span>
          </p>
        )}

        <AIDecisionSummary
          decision={item}
          headingLevel={4}
          title="Yapay zekâ değerlendirmesi"
          actions={
            approvable ? (
              <>
                <Button
                  onClick={() => {
                    setConfirmOpen(true);
                  }}
                  loading={approve.isPending}
                  disabled={problem?.kind === 'stale' || problem?.kind === 'forbidden'}
                  aria-describedby={`d-${item.id}`}
                >
                  Onayla ve uygula
                </Button>
                {incidentPath && (
                  <Link className={buttonClassName('ghost')} to={incidentPath}>
                    Kaydı incele
                  </Link>
                )}
              </>
            ) : (
              <>
                <p className={styles.noticeText}>
                  {item.outcome === 'escalated'
                    ? 'İnsana yönlendirilen kararlar doğrudan onaylanamaz; kaydı inceleyip elle karar verin.'
                    : 'Bu karar onaylanamaz.'}
                </p>
                {incidentPath && (
                  <Link className={buttonClassName('secondary', 'sm')} to={incidentPath}>
                    Kaydı incele
                  </Link>
                )}
              </>
            )
          }
        />
      </article>

      <ConfirmDialog
        open={confirmOpen}
        title="Öneri onaylansın mı?"
        description={`${item.incident_number ?? ''} ${title}`.trim()}
        confirmLabel="Onayla ve uygula"
        tone={change?.kind === 'urgent_raise' ? 'danger' : 'primary'}
        busy={approve.isPending}
        onConfirm={() => {
          approve.mutate();
        }}
        onCancel={() => {
          setConfirmOpen(false);
        }}
      >
        <div className="stack">
          {changes.length > 0 ? (
            <>
              <p style={{ margin: 0 }}>Onayladığınızda kayda şu değişiklikler uygulanır:</p>
              <ul className={styles.changeList}>
                {changes.map((r) => (
                  <li key={r.field}>
                    <strong>{fieldLabel(r.field)}:</strong>{' '}
                    {r.state === 'unknown' ? '' : `${displayValue(r.field, r.current)} → `}
                    {displayValue(r.field, r.proposed)}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p style={{ margin: 0 }}>Önerilen değerler kayıtla aynı; onay kararı kapatır.</p>
          )}
          {change?.kind === 'urgent_raise' && (
            <p className={styles.problemTitle} style={{ margin: 0 }}>
              Bu onay önceliği {change.proposed} seviyesine yükseltir.
            </p>
          )}
          {change?.kind === 'urgent_downgrade' && (
            <p className={styles.warningText} style={{ margin: 0 }}>
              Bu onay önceliği {change.current} seviyesinden {change.proposed} seviyesine düşürür.
            </p>
          )}
          {staleHint && (
            <p className={styles.warningText} style={{ margin: 0 }}>
              Kayıt öneriden sonra değişti; sunucu onayı reddedebilir.
            </p>
          )}
          <p className="muted" style={{ margin: 0 }}>
            Onayınız denetim kaydına adınızla yazılır.
          </p>
        </div>
      </ConfirmDialog>
    </li>
  );
}
