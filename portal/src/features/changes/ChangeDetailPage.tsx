import { useId } from 'react';
import { Link, useParams } from 'react-router-dom';
import { isApiError } from '@/api/errors';
import type { Change } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { ErrorState, Spinner } from '@/components';
import { formatDateTime } from '@/lib/format';
import { groupLabel } from '@/lib/taxonomy';
import { usePageTitle } from '@/lib/usePageTitle';
import styles from './Changes.module.css';
import { CabPanel } from './components/CabPanel';
import { ChangeStateBadge, ChangeTypeBadge, RiskBadge } from './components/ChangeBadges';
import { ConflictList } from './components/ConflictList';
import { ImpactSummary } from './components/ImpactSummary';
import { RiskPanel } from './components/RiskPanel';
import { StateStepper } from './components/StateStepper';
import { TransitionPanel } from './components/TransitionPanel';
import {
  useChange,
  useChangeApprovals,
  useChangeConflicts,
  useChangeDecisions,
  useChangeImpact,
} from './hooks';
import { TERMINAL_STATES, canAssessRisk, isChangeManager } from './rules';

/** /degisiklikler/:id — değişiklik ayrıntısı, risk, CAB ve geçişler (sahibi: changes) */
export default function ChangeDetailPage() {
  const { id = '' } = useParams();
  const query = useChange(id);
  const change = query.data;
  usePageTitle(change ? `${change.number} · ${change.title}` : 'Değişiklik ayrıntısı');

  if (query.isPending) return <Spinner block label="Değişiklik yükleniyor…" />;
  if (query.isError || !change) {
    const err = query.error;
    const notFound = isApiError(err) && err.isNotFound;
    return (
      <div className="page">
        <Link to="/degisiklikler">← Değişikliklere dön</Link>
        <h1 className="visually-hidden">Değişiklik ayrıntısı</h1>
        <ErrorState
          error={err}
          title={notFound ? 'Değişiklik bulunamadı' : undefined}
          onRetry={notFound ? undefined : () => void query.refetch()}
        />
      </div>
    );
  }
  return <ChangeDetail change={change} />;
}

function ChangeDetail({ change }: { change: Change }) {
  const { user, roles, hasRole } = useAuth();
  const actor = { username: user?.username ?? '', roles };
  const approvals = useChangeApprovals(change.id, change.change_type !== 'standard');
  const decisions = useChangeDecisions(change.id);
  const impact = useChangeImpact(change.id, change.affected_ci_ids.length > 0);
  const checkConflicts =
    !!change.planned_start &&
    !!change.planned_end &&
    !TERMINAL_STATES.includes(change.state) &&
    change.state !== 'review' &&
    change.state !== 'failed';
  const conflicts = useChangeConflicts(change.id, checkConflicts);
  const detailQuery = useChange(change.id);
  const fieldsId = useId();
  const plansId = useId();
  const impactId = useId();

  const reload = async () => {
    await Promise.all([detailQuery.refetch(), approvals.refetch(), decisions.refetch()]);
  };

  return (
    <div className="page">
      <header className="stack" style={{ gap: 'var(--space-3)' }}>
        <Link to="/degisiklikler">← Değişikliklere dön</Link>
        <div className="row">
          <span className="muted">{change.number}</span>
          <ChangeTypeBadge type={change.change_type} />
          <ChangeStateBadge state={change.state} />
          <RiskBadge level={change.risk_level} score={change.risk_score} />
        </div>
        <h1 style={{ margin: 0 }}>{change.title}</h1>
        <StateStepper change={change} />
      </header>

      <div className={styles.detailGrid}>
        <div className="stack">
          <section className="card stack" aria-labelledby={fieldsId}>
            <h2 id={fieldsId} className={styles.sectionTitle}>
              Ayrıntılar
            </h2>
            {change.description && (
              <p className="prewrap" style={{ margin: 0 }}>
                {change.description}
              </p>
            )}
            <dl className={styles.fields}>
              <dt>Talep eden</dt>
              <dd>{change.requested_by}</dd>
              <dt>Uygulayıcı</dt>
              <dd>{change.assignee ?? <span className="muted">Talep eden</span>}</dd>
              <dt>Atama grubu</dt>
              <dd>{change.assignment_group ? groupLabel(change.assignment_group) : '—'}</dd>
              <dt>Planlanan pencere</dt>
              <dd>
                {change.planned_start
                  ? `${formatDateTime(change.planned_start)} – ${formatDateTime(change.planned_end)}`
                  : 'Planlanmadı'}
              </dd>
              {change.actual_start && (
                <>
                  <dt>Gerçekleşen</dt>
                  <dd>
                    {formatDateTime(change.actual_start)} – {formatDateTime(change.actual_end)}
                  </dd>
                </>
              )}
              <dt>Güncellendi</dt>
              <dd>
                {formatDateTime(change.updated_at)} · sürüm {change.version}
              </dd>
            </dl>
            {checkConflicts && conflicts.data && conflicts.data.length > 0 && (
              <div role="status" className={styles.warningBox}>
                <strong>Zamanlama çakışmaları</strong>
                <ConflictList conflicts={conflicts.data} />
              </div>
            )}
          </section>

          <section className="card stack" aria-labelledby={plansId}>
            <h2 id={plansId} className={styles.sectionTitle}>
              Planlar
            </h2>
            <Plan title="Uygulama planı" text={change.implementation_plan} />
            <Plan title="Geri dönüş planı" text={change.backout_plan} />
            <Plan title="Test planı" text={change.test_plan} />
            {change.review_notes && (
              <Plan title="Uygulama sonrası gözden geçirme (PIR)" text={change.review_notes} />
            )}
          </section>

          <section className="card stack" aria-labelledby={impactId}>
            <h2 id={impactId} className={styles.sectionTitle}>
              Etki analizi
            </h2>
            {change.affected_ci_ids.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                Etkilenen CI belirtilmedi.
              </p>
            ) : impact.isError ? (
              <ErrorState error={impact.error} />
            ) : impact.data ? (
              <>
                <p style={{ margin: 0 }}>
                  Değişen CI’lar:{' '}
                  {impact.data.root_ci_ids.map((ciId, i) => (
                    <span key={ciId}>
                      {i > 0 && ', '}
                      <Link to={`/cmdb/${encodeURIComponent(ciId)}`}>{`CI ${i + 1}`}</Link>
                    </span>
                  ))}
                </p>
                <ImpactSummary impact={impact.data} />
              </>
            ) : (
              <Spinner label="Etki hesaplanıyor…" />
            )}
          </section>
        </div>

        <div className="stack">
          <TransitionPanel change={change} actor={actor} onReload={reload} />
          <RiskPanel
            change={change}
            decisions={decisions.data}
            decisionsError={decisions.error}
            decisionsPending={decisions.isPending}
            canAssess={canAssessRisk(change, roles)}
            canApproveDecision={isChangeManager(roles)}
          />
          <CabPanel
            change={change}
            actor={actor}
            canVote={hasRole('cab', 'change_manager')}
            approvals={approvals.data}
            approvalsError={approvals.error}
            approvalsPending={approvals.isPending}
          />
        </div>
      </div>
    </div>
  );
}

function Plan({ title, text }: { title: string; text: string }) {
  return (
    <div className="stack" style={{ gap: 'var(--space-1)' }}>
      <h3 className={styles.subTitle}>{title}</h3>
      {text ? (
        <p className="prewrap" style={{ margin: 0 }}>
          {text}
        </p>
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          Belirtilmedi.
        </p>
      )}
    </div>
  );
}
