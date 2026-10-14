import { useId } from 'react';
import { Link } from 'react-router-dom';
import { isApiError } from '@/api/errors';
import type { AIDecision, Change } from '@/api/types';
import { Button, ConfidenceMeter, ErrorState, OutcomeBadge, Spinner, useToast } from '@/components';
import { formatDateTime } from '@/lib/format';
import { OUTCOME_DESCRIPTIONS, OUTCOME_LABELS, RISK_LEVEL_LABELS } from '@/lib/labels';
import styles from '../Changes.module.css';
import { useApproveRiskDecision, useAssessRisk } from '../hooks';
import { latestRiskDecision, riskInsight } from '../risk';
import { RiskBadge } from './ChangeBadges';

export interface RiskPanelProps {
  change: Change;
  decisions: AIDecision[] | undefined;
  decisionsError: unknown;
  decisionsPending: boolean;
  canAssess: boolean;
  canApproveDecision: boolean;
}

/**
 * Risk paneli: değişikliğin risk skoru + son yapay zekâ risk kararı (deterministik döküm,
 * anlatı, kontrol listesi, sonuç ve güven). Yapay zekâ yalnızca risk alanlarını önerir;
 * riski düşüren öneriyi değişiklik yöneticisi onaylar.
 */
export function RiskPanel({
  change,
  decisions,
  decisionsError,
  decisionsPending,
  canAssess,
  canApproveDecision,
}: RiskPanelProps) {
  const headingId = useId();
  const toast = useToast();
  const assess = useAssessRisk(change.id);
  const approve = useApproveRiskDecision(change.id);
  const decision = decisions ? latestRiskDecision(decisions) : null;

  const runAssess = () => {
    assess.mutate(undefined, {
      onSuccess: (d) => {
        toast.show({
          title: `Yapay zekâ risk kararı: ${OUTCOME_LABELS[d.outcome]}`,
          description: OUTCOME_DESCRIPTIONS[d.outcome],
          tone:
            d.outcome === 'auto_applied' ? 'success' : d.outcome === 'rejected' ? 'error' : 'info',
        });
      },
      onError: (e) => {
        toast.show({
          title: 'Risk değerlendirilemedi',
          description: isApiError(e) ? e.detail : undefined,
          tone: 'error',
        });
      },
    });
  };

  const runApprove = (id: string) => {
    approve.mutate(id, {
      onSuccess: () => {
        toast.show({ title: 'Risk önerisi onaylandı ve uygulandı', tone: 'success' });
      },
      onError: (e) => {
        toast.show({
          title: isApiError(e) && e.isConflict ? 'Öneri bayatladı' : 'Öneri onaylanamadı',
          description: isApiError(e)
            ? `${e.detail}${e.isConflict ? ' Riski yeniden değerlendirin.' : ''}`
            : undefined,
          tone: 'error',
        });
      },
    });
  };

  return (
    <section className="card stack" aria-labelledby={headingId}>
      <div className={styles.panelHead}>
        <h2 id={headingId} className={styles.sectionTitle}>
          Risk değerlendirmesi
        </h2>
        {canAssess && (
          <Button size="sm" variant="secondary" loading={assess.isPending} onClick={runAssess}>
            Riski değerlendir
          </Button>
        )}
      </div>
      <div className="row">
        <span>Mevcut risk:</span>
        <RiskBadge level={change.risk_level} score={change.risk_score} />
      </div>
      {decisionsError ? (
        <ErrorState error={decisionsError} />
      ) : decisionsPending ? (
        <Spinner label="Yapay zekâ kararları yükleniyor…" />
      ) : decision ? (
        <RiskDecision
          decision={decision}
          canApprove={canApproveDecision}
          approving={approve.isPending}
          onApprove={runApprove}
        />
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          Bu değişiklik için henüz yapay zekâ risk değerlendirmesi yok.
          {canAssess ? ' “Riski değerlendir” ile başlatabilirsiniz.' : ''}
        </p>
      )}
    </section>
  );
}

function RiskDecision({
  decision,
  canApprove,
  approving,
  onApprove,
}: {
  decision: AIDecision;
  canApprove: boolean;
  approving: boolean;
  onApprove: (id: string) => void;
}) {
  const insight = riskInsight(decision);
  const pending = !decision.approved_by && decision.outcome === 'needs_approval';
  const total = insight.factors.reduce((sum, f) => sum + f.points, 0);
  return (
    <article className="stack" aria-label="Yapay zekâ risk kararı">
      <div className="row">
        <OutcomeBadge outcome={decision.outcome} />
        {insight.proposedScore !== null && (
          <span>
            Önerilen skor: <strong>{insight.proposedScore}</strong>
            {insight.proposedLevel ? ` (${RISK_LEVEL_LABELS[insight.proposedLevel]})` : ''}
            {insight.currentScore !== null ? ` · önceki: ${insight.currentScore}` : ''}
          </span>
        )}
      </div>
      <ConfidenceMeter value={decision.confidence} />
      {insight.lowersRisk && (
        <p className={styles.warningBox} style={{ margin: 0 }}>
          Bu öneri riski düşürüyor; risk düşürme hiçbir zaman otomatik uygulanmaz.
        </p>
      )}
      {insight.factors.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className={styles.caption}>Deterministik risk dökümü</caption>
            <thead>
              <tr>
                <th scope="col">Bileşen</th>
                <th scope="col">Puan</th>
              </tr>
            </thead>
            <tbody>
              {insight.factors.map((f) => (
                <tr key={f.key}>
                  <td>{f.label}</td>
                  <td>{f.points > 0 ? `+${f.points}` : f.points}</td>
                </tr>
              ))}
              {insight.adjustment !== 0 && (
                <tr>
                  <td>
                    Yapay zekâ düzeltmesi
                    {insight.justification && (
                      <span className="muted prewrap"> — {insight.justification}</span>
                    )}
                  </td>
                  <td>{insight.adjustment > 0 ? `+${insight.adjustment}` : insight.adjustment}</td>
                </tr>
              )}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Deterministik skor</th>
                <td>
                  <strong>{insight.deterministicScore ?? total}</strong>
                  {insight.deterministicLevel
                    ? ` (${RISK_LEVEL_LABELS[insight.deterministicLevel]})`
                    : ''}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {insight.notes.map((n) => (
        <p key={n} className="muted" style={{ margin: 0 }}>
          Not: {n}
        </p>
      ))}
      <div className="stack" style={{ gap: 'var(--space-1)' }}>
        <h3 className={styles.subTitle}>Yapay zekâ anlatısı</h3>
        <p className="prewrap" style={{ margin: 0 }}>
          {insight.narrative || 'Anlatı üretilmedi.'}
        </p>
        {insight.llmError && (
          <p className="muted" style={{ margin: 0 }}>
            Model yanıtı kullanılamadı; karar insana yönlendirildi.
          </p>
        )}
      </div>
      {insight.checklist.length > 0 && (
        <div className="stack" style={{ gap: 'var(--space-1)' }}>
          <h3 className={styles.subTitle}>Uygulama öncesi kontrol listesi</h3>
          <ul style={{ margin: 0 }}>
            {insight.checklist.map((item, i) => (
              <li key={i} className="prewrap">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
      {decision.sources.length > 0 && (
        <p style={{ margin: 0 }}>
          Dayanak CI’lar:{' '}
          {decision.sources.map((id, i) => (
            <span key={id}>
              {i > 0 && ', '}
              <Link to={`/cmdb/${encodeURIComponent(id)}`}>CI {i + 1}</Link>
            </span>
          ))}
        </p>
      )}
      {insight.policyReason && (
        <p className="muted prewrap" style={{ margin: 0 }}>
          Politika: {insight.policyReason}
        </p>
      )}
      <p className="muted" style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
        {decision.model} · {formatDateTime(decision.created_at)}
        {decision.approved_by &&
          ` · Onaylayan: ${decision.approved_by} (${formatDateTime(decision.approved_at)})`}
      </p>
      {pending &&
        (canApprove ? (
          <div className="row">
            <Button
              loading={approving}
              onClick={() => {
                onApprove(decision.id);
              }}
            >
              Öneriyi onayla
            </Button>
            <span className="muted">Yalnızca risk skoru uygulanır; durum değişmez.</span>
          </div>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            Bu öneri için değişiklik yöneticisi onayı gerekir.
          </p>
        ))}
    </article>
  );
}
