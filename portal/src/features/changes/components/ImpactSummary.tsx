import { Link } from 'react-router-dom';
import type { ImpactAnalysis } from '@/api/types';
import { Badge } from '@/components';
import {
  CI_CLASS_LABELS,
  CI_ENVIRONMENT_LABELS,
  CI_RELATIONSHIP_LABELS,
  criticalityLabel,
} from '@/lib/labels';
import styles from '../Changes.module.css';

const criticalityTone = (c: number | null) =>
  c === 1 ? 'danger' : c === 2 ? 'warning' : c === 3 ? 'info' : 'neutral';

/** Etki analizi özeti: sayı, en yüksek kritiklik, iş hizmetleri ve etkilenen CI tablosu */
export function ImpactSummary({ impact }: { impact: ImpactAnalysis }) {
  const services = impact.business_services.map((s) => s.name);
  return (
    <div className="stack">
      <div className="row">
        <span>{`Etkilenen CI: ${impact.affected.length}`}</span>
        <span aria-hidden="true">·</span>
        <span>En yüksek kritiklik:</span>
        <Badge tone={criticalityTone(impact.highest_criticality)} dot>
          {criticalityLabel(impact.highest_criticality)}
        </Badge>
      </div>
      <p style={{ margin: 0 }}>
        {services.length > 0
          ? `İş hizmetleri: ${services.join(', ')}`
          : 'Etkilenen iş hizmeti yok.'}
      </p>
      {impact.affected.length > 0 ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="visually-hidden">Etkilenen yapılandırma öğeleri</caption>
            <thead>
              <tr>
                <th scope="col">CI</th>
                <th scope="col">Sınıf</th>
                <th scope="col">Ortam</th>
                <th scope="col">Kritiklik</th>
                <th scope="col">Uzaklık</th>
              </tr>
            </thead>
            <tbody>
              {impact.affected.map((a) => (
                <tr key={a.ci.id}>
                  <td>
                    <Link to={`/cmdb/${encodeURIComponent(a.ci.id)}`}>{a.ci.name}</Link>
                    <br />
                    <span className="muted">{CI_RELATIONSHIP_LABELS[a.relationship]}</span>
                  </td>
                  <td>{CI_CLASS_LABELS[a.ci.ci_class]}</td>
                  <td>{CI_ENVIRONMENT_LABELS[a.ci.environment]}</td>
                  <td>{criticalityLabel(a.ci.criticality)}</td>
                  <td>{a.depth === 1 ? 'Doğrudan' : `${a.depth}. derece`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          Bu CI’lara bağımlı başka öğe bulunmadı.
        </p>
      )}
    </div>
  );
}
