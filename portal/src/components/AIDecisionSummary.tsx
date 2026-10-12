import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { AIDecision } from '@/api/types';
import { formatDateTime } from '@/lib/format';
import styles from './AIDecisionSummary.module.css';
import { ConfidenceMeter } from './ConfidenceMeter';
import { OutcomeBadge } from './OutcomeBadge';

export interface AIDecisionSummaryProps {
  decision: Pick<
    AIDecision,
    | 'outcome'
    | 'confidence'
    | 'rationale'
    | 'sources'
    | 'agent'
    | 'action'
    | 'model'
    | 'created_at'
    | 'approved_by'
    | 'approved_at'
  >;
  /** Onay/ret gibi eylem butonları */
  actions?: ReactNode;
  /** Kart başlığı; varsayılan "Yapay zekâ önerisi" */
  title?: string;
  headingLevel?: 2 | 3 | 4;
}

/**
 * Her yapay zekâ kararında zorunlu bilgiler: sonuç, güven, gerekçe ve bilgi bankası kaynakları.
 * Gerekçe düz metindir (HTML olarak yorumlanmaz).
 */
export function AIDecisionSummary({
  decision,
  actions,
  title = 'Yapay zekâ önerisi',
  headingLevel = 3,
}: AIDecisionSummaryProps) {
  const Heading = `h${headingLevel}` as const;
  return (
    <article className={styles.card} aria-label={title}>
      <div className={styles.head}>
        <Heading style={{ margin: 0, fontSize: 'var(--text-md)' }}>{title}</Heading>
        <OutcomeBadge outcome={decision.outcome} />
      </div>
      <ConfidenceMeter value={decision.confidence} />
      <div className={styles.section}>
        <p className={styles.sectionTitle}>Gerekçe</p>
        <p className={`${styles.rationale ?? ''} prewrap`}>
          {decision.rationale || 'Gerekçe belirtilmedi.'}
        </p>
      </div>
      <div className={styles.section}>
        <p className={styles.sectionTitle}>Bilgi bankası kaynakları</p>
        {decision.sources.length > 0 ? (
          <ul className={styles.sources}>
            {decision.sources.map((id) => (
              <li key={id}>
                <Link to={`/bilgi-bankasi/${encodeURIComponent(id)}`}>{id}</Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            Kaynak gösterilmedi.
          </p>
        )}
      </div>
      <p className={styles.meta}>
        {decision.agent} · {decision.action} · {decision.model} ·{' '}
        {formatDateTime(decision.created_at)}
        {decision.approved_by &&
          ` · Onaylayan: ${decision.approved_by} (${formatDateTime(decision.approved_at)})`}
      </p>
      {actions && <div className={styles.actions}>{actions}</div>}
    </article>
  );
}
