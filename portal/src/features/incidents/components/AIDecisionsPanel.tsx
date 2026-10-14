import { Link } from 'react-router-dom';
import type { AIDecision, Incident } from '@/api/types';
import {
  AIDecisionSummary,
  Badge,
  EmptyState,
  ErrorState,
  Spinner,
  buttonClassName,
} from '@/components';
import { formatDateTime } from '@/lib/format';
import {
  buildDiff,
  displayValue,
  fieldLabel,
  parseDecisionOutput,
  type DiffState,
} from '../decisionOutput';
import styles from './AIDecisionsPanel.module.css';

const ACTION_TITLES: Record<string, string> = {
  triage: 'Sınıflandırma (triage)',
};

const DIFF_STATE: Record<DiffState, { text: string; tone: 'success' | 'warning' | 'neutral' }> = {
  applied: { text: 'Uygulandı', tone: 'success' },
  pending: { text: 'Uygulanmadı', tone: 'warning' },
  same: { text: 'Değişiklik yok', tone: 'neutral' },
};

const PENDING_OUTCOMES: ReadonlySet<AIDecision['outcome']> = new Set([
  'suggested',
  'needs_approval',
]);

export interface AIDecisionsPanelProps {
  incident: Incident;
  decisions: AIDecision[] | undefined;
  isPending: boolean;
  error: unknown;
  onRetry: () => void;
  canApprove: boolean;
}

export function AIDecisionsPanel({
  incident,
  decisions,
  isPending,
  error,
  onRetry,
  canApprove,
}: AIDecisionsPanelProps) {
  if (isPending) return <Spinner block label="Yapay zekâ kararları yükleniyor…" />;
  if (error) return <ErrorState error={error} title="Kararlar yüklenemedi" onRetry={onRetry} />;
  if (!decisions || decisions.length === 0) {
    return (
      <EmptyState
        headingLevel={3}
        icon="✦"
        title="Henüz yapay zekâ kararı yok"
        description="“Yapay zekâ ile sınıflandır” ile tür, kategori, hizmet, etki ve aciliyet önerisi alabilirsiniz. Öneriler politika izin vermedikçe uygulanmaz."
      />
    );
  }
  return (
    <ol className={styles.list} aria-label="Yapay zekâ kararları (en yeni önce)">
      {decisions.map((d) => (
        <li key={d.id}>
          <DecisionCard decision={d} incident={incident} canApprove={canApprove} />
        </li>
      ))}
    </ol>
  );
}

function DecisionCard({
  decision,
  incident,
  canApprove,
}: {
  decision: AIDecision;
  incident: Incident;
  canApprove: boolean;
}) {
  const parsed = parseDecisionOutput(decision.output);
  const diff = buildDiff(parsed, incident);
  const pending = PENDING_OUTCOMES.has(decision.outcome) && !decision.approved_at;
  const stale =
    pending &&
    decision.incident_version !== null &&
    decision.incident_version !== undefined &&
    decision.incident_version !== incident.version;
  const title = `${ACTION_TITLES[decision.action] ?? 'Yapay zekâ kararı'} · ${formatDateTime(decision.created_at)}`;

  return (
    <AIDecisionSummary
      decision={decision}
      title={title}
      headingLevel={3}
      actions={
        <div className={styles.extras}>
          {parsed.policyReason && (
            <div className={styles.section}>
              <p className={styles.sectionTitle}>Politika sonucu</p>
              <p className="prewrap" style={{ margin: 0 }}>
                {parsed.policyReason}
              </p>
            </div>
          )}

          {parsed.error && (
            <p className={styles.notice} data-tone="warning">
              {parsed.error} Kayıt insana yönlendirildi.
            </p>
          )}

          {diff.length > 0 && (
            <div className={styles.section}>
              <p className={styles.sectionTitle} id={`diff-${decision.id}`}>
                Önerilen ve güncel değerler
              </p>
              <div className={styles.tableWrap}>
                <table className={styles.diff} aria-labelledby={`diff-${decision.id}`}>
                  <thead>
                    <tr>
                      <th scope="col">Alan</th>
                      <th scope="col">Güncel</th>
                      <th scope="col">Önerilen</th>
                      <th scope="col">Durum</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diff.map((row) => (
                      <tr key={row.field} data-state={row.state}>
                        <th scope="row">{fieldLabel(row.field)}</th>
                        <td data-label="Güncel">{displayValue(row.field, row.current)}</td>
                        <td
                          data-label="Önerilen"
                          className={row.state === 'pending' ? styles.changed : undefined}
                        >
                          {displayValue(row.field, row.proposed)}
                        </td>
                        <td data-label="Durum">
                          <Badge tone={DIFF_STATE[row.state].tone}>
                            {DIFF_STATE[row.state].text}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {parsed.suggestedResolution && (
            <div className={styles.section}>
              <p className={styles.sectionTitle}>Önerilen çözüm</p>
              <p className="prewrap" style={{ margin: 0 }}>
                {parsed.suggestedResolution}
              </p>
            </div>
          )}

          {parsed.droppedKbIds.length > 0 && (
            <p className={styles.notice} data-tone="warning">
              Doğrulanamayan bilgi bankası kaynakları atıldı (aramada bulunmadı, güven düşürüldü):{' '}
              {parsed.droppedKbIds.join(', ')}
            </p>
          )}

          {parsed.offTaxonomy.length > 0 && (
            <div className={styles.notice} data-tone="warning">
              <p style={{ margin: 0 }}>
                Sınıf listesinde olmayan değerler uygulanmadı (güven düşürüldü):
              </p>
              <ul className={styles.inlineList}>
                {parsed.offTaxonomy.map((o) => (
                  <li key={o.field}>
                    {fieldLabel(o.field)}: “{o.value}”
                  </li>
                ))}
              </ul>
            </div>
          )}

          {stale && (
            <p className={styles.notice} data-tone="danger" role="note">
              Bu öneri kaydın eski bir sürümüne (v{decision.incident_version}) dayanıyor; kayıt şu
              an v{incident.version}. Onaylanırsa “yeniden triage gerekli” hatası alınır — kaydı
              yeniden sınıflandırın.
            </p>
          )}

          {pending && canApprove && (
            <Link className={buttonClassName('secondary', 'sm')} to="/onaylar">
              Onay kutusunda aç
            </Link>
          )}
        </div>
      }
    />
  );
}
