import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { improvementsApi, queryKeys, type ImprovementListQuery } from '@/api';
import type { ImprovementSource, ImprovementState } from '@/api/types';
import { Badge, EmptyState, ErrorState, Select, Spinner, optionsFrom } from '@/components';
import { useKpiDefinitions } from '@/features/kpi/useKpiDefinitions';
import { IMPROVEMENT_SOURCE_LABELS, IMPROVEMENT_STATE_LABELS, actorLabel } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { ImprovementStateBadge } from './ImprovementStateBadge';
import { kpiName, scopeText } from './KpiScope';
import { TargetBadge } from './TargetBadge';
import { unitFor, valueText } from './model';
import styles from './Improvements.module.css';

const LIMIT = 100;
const isState = (v: string): v is ImprovementState => v in IMPROVEMENT_STATE_LABELS;
const isSource = (v: string): v is ImprovementSource => v in IMPROVEMENT_SOURCE_LABELS;

/** /iyilestirmeler — sürekli iyileştirme (CSI) kaydı (sahibi: improvements). GET /improvements */
export default function ImprovementListPage() {
  usePageTitle('İyileştirmeler');
  const [state, setState] = useState<ImprovementState | ''>('');
  const [source, setSource] = useState<ImprovementSource | ''>('');
  const query: ImprovementListQuery = {
    limit: LIMIT,
    ...(state ? { state } : {}),
    ...(source ? { source } : {}),
  };
  const list = useQuery({
    queryKey: queryKeys.improvements.list(query),
    queryFn: ({ signal }) => improvementsApi.list(query, signal),
  });
  const defs = useKpiDefinitions().data;

  let content;
  if (list.isPending) content = <Spinner block label="İyileştirmeler yükleniyor…" showLabel />;
  else if (list.isError) {
    content = (
      <ErrorState
        error={list.error}
        title="İyileştirme kaydı yüklenemedi"
        onRetry={() => void list.refetch()}
      />
    );
  } else if (list.data.length === 0) {
    content = (
      <EmptyState
        icon="↗"
        title={state || source ? 'Bu süzgeçle iyileştirme yok' : 'Henüz iyileştirme kaydı yok'}
        description="İyileştirmeler KPI eğilimlerinden, sorun kayıtlarından ve değişiklik sonrası gözden geçirmelerden doğar."
      />
    );
  } else {
    content = (
      <>
        <p className={styles.count} aria-live="polite">
          {list.data.length} iyileştirme
        </p>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="visually-hidden">İyileştirme kaydı</caption>
            <thead>
              <tr>
                <th scope="col">Numara</th>
                <th scope="col">Başlık</th>
                <th scope="col">Durum</th>
                <th scope="col">Ölçüt</th>
                <th scope="col">Taban → hedef</th>
                <th scope="col">Ölçülen</th>
                <th scope="col">Öncelik puanı</th>
                <th scope="col">Sahip</th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((imp) => {
                const unit = unitFor(imp, defs);
                const scope = scopeText(imp);
                return (
                  <tr key={imp.id}>
                    <td className={styles.number}>{imp.number}</td>
                    <th scope="row">
                      <div className={styles.cellStack}>
                        <Link to={`/iyilestirmeler/${encodeURIComponent(imp.id)}`}>
                          {imp.title}
                        </Link>
                        <span className={styles.meta}>{IMPROVEMENT_SOURCE_LABELS[imp.source]}</span>
                        {imp.detected_by.startsWith('ai:') && (
                          <Badge tone="accent">Yapay zekâ önerisi</Badge>
                        )}
                      </div>
                    </th>
                    <td>
                      <ImprovementStateBadge state={imp.state} />
                    </td>
                    <td>
                      <div className={styles.cellStack}>
                        <span>{kpiName(imp, defs)}</span>
                        {scope && <span className={styles.meta}>{scope}</span>}
                      </div>
                    </td>
                    <td className={styles.num}>
                      {valueText(imp.baseline_value, unit)} → {valueText(imp.target_value, unit)}
                    </td>
                    <td>
                      <div className={styles.cellStack}>
                        <span className={styles.num}>{valueText(imp.measured_value, unit)}</span>
                        <TargetBadge met={imp.target_met} />
                      </div>
                    </td>
                    <td className={styles.num}>{Math.round(imp.priority_score)}</td>
                    <td>{imp.owner ? actorLabel(imp.owner) : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </>
    );
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>İyileştirmeler</h1>
          <p className="muted" style={{ margin: 0 }}>
            Sürekli iyileştirme kaydı: her girişim bir KPI tabanı, hedef ve uygulama sonrası ölçümle
            izlenir. Onay ve kapatma insan kararıdır.
          </p>
        </div>
      </header>
      <div className={styles.toolbar}>
        <Select
          label="Durum"
          placeholder="Tüm durumlar"
          options={optionsFrom(IMPROVEMENT_STATE_LABELS)}
          value={state}
          onChange={(e) => {
            const v = e.target.value;
            setState(isState(v) ? v : '');
          }}
        />
        <Select
          label="Kaynak"
          placeholder="Tüm kaynaklar"
          options={optionsFrom(IMPROVEMENT_SOURCE_LABELS)}
          value={source}
          onChange={(e) => {
            const v = e.target.value;
            setSource(isSource(v) ? v : '');
          }}
        />
      </div>
      {content}
    </div>
  );
}
