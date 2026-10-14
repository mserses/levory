import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { kpiApi, queryKeys, type BreachForecast, type ForecastQuery } from '@/api';
import { Badge, EmptyState, ErrorState, PriorityBadge, Select, Spinner } from '@/components';
import { formatDateTime, formatDuration, formatPercent } from '@/lib/format';
import { FORECAST_BASIS_LABELS } from '@/lib/labels';
import { groupLabel, taxonomyOptions } from '@/lib/taxonomy';
import { usePageTitle } from '@/lib/usePageTitle';
import { isPriority, rankForecasts } from './model';
import styles from './Kpi.module.css';

const LIMIT = 100;
const MIN_PROBABILITY = [
  { value: '0.25', label: '%25 ve üstü' },
  { value: '0.5', label: '%50 ve üstü' },
  { value: '0.75', label: '%75 ve üstü' },
];

function remainingText(hours: number): string {
  const text = formatDuration(Math.abs(hours) * 3600);
  return hours < 0 ? `${text} geçti` : `${text} kaldı`;
}

/** /sla-tahmin — açık olayların çözüm SLA'sı ihlal olasılığı (sahibi: kpi). GET /sla/forecast */
export default function SlaForecastPage() {
  usePageTitle('SLA tahmini');
  const [group, setGroup] = useState('');
  const [minProbability, setMinProbability] = useState('');
  const query: ForecastQuery = {
    limit: LIMIT,
    ...(group ? { assignment_group: group } : {}),
    ...(minProbability ? { min_probability: Number(minProbability) } : {}),
  };
  const forecast = useQuery({
    queryKey: queryKeys.sla.forecast(query),
    queryFn: ({ signal }) => kpiApi.forecast(query, signal),
  });

  let content;
  if (forecast.isPending) content = <Spinner block label="Tahminler yükleniyor…" showLabel />;
  else if (forecast.isError) {
    content = (
      <ErrorState
        error={forecast.error}
        title="SLA tahmini yüklenemedi"
        onRetry={() => void forecast.refetch()}
      />
    );
  } else if (forecast.data.items.length === 0) {
    content = (
      <EmptyState
        icon="✓"
        title="Tahmin edilecek açık olay yok"
        description="Süzgeçleri gevşetmeyi deneyin."
      />
    );
  } else {
    const items = rankForecasts(forecast.data.items);
    content = (
      <>
        <p className={styles.windowNote} aria-live="polite">
          {items.length} açık olay · karşılaştırma için son {forecast.data.lookback_days} gün, en az{' '}
          {forecast.data.min_sample} çözülmüş olay · {formatDateTime(forecast.data.generated_at)}
        </p>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="visually-hidden">SLA ihlal tahmini</caption>
            <thead>
              <tr>
                <th scope="col">Olay</th>
                <th scope="col">Öncelik</th>
                <th scope="col">Grup</th>
                <th scope="col">İhlal olasılığı</th>
                <th scope="col">Hedefe</th>
                <th scope="col">Beklenen çözüm</th>
                <th scope="col">Dayanak</th>
                <th scope="col">Gerekçeler</th>
              </tr>
            </thead>
            <tbody>
              {items.map((f) => (
                <ForecastRow key={f.incident_id} forecast={f} />
              ))}
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
          <h1>SLA tahmini</h1>
          <p className="muted" style={{ margin: 0 }}>
            Açık olayların çözüm hedefini aşma olasılığı: benzer geçmiş olaylar (Kaplan–Meier),
            olayın yaşı ve grubun şu anki kuyruğu. Her tahmin gerekçesi ve örneklem büyüklüğüyle
            gösterilir.
          </p>
        </div>
      </header>
      <div className={styles.toolbar}>
        <Select
          label="Çözüm grubu"
          placeholder="Tüm gruplar"
          options={taxonomyOptions('assignment_group')}
          value={group}
          onChange={(e) => {
            setGroup(e.target.value);
          }}
        />
        <Select
          label="En düşük olasılık"
          placeholder="Tümü"
          options={MIN_PROBABILITY}
          value={minProbability}
          onChange={(e) => {
            setMinProbability(e.target.value);
          }}
        />
      </div>
      {content}
    </div>
  );
}

function ForecastRow({ forecast: f }: { forecast: BreachForecast }) {
  const reasons = [...f.reasons].sort((a, b) => b.weight - a.weight);
  return (
    <tr>
      <th scope="row">
        <Link to={`/kayitlar/${encodeURIComponent(f.incident_id)}`}>{f.number}</Link>
        {f.paused && (
          <div>
            <Badge tone="info">SLA duraklatıldı</Badge>
          </div>
        )}
      </th>
      <td>{isPriority(f.priority) ? <PriorityBadge priority={f.priority} /> : f.priority}</td>
      <td>{f.assignment_group ? groupLabel(f.assignment_group) : '—'}</td>
      <td>
        {f.probability === null ? (
          <span className="muted">Tahmin yok</span>
        ) : (
          <div className={styles.prob}>
            <strong className={styles.num}>{formatPercent(f.probability)}</strong>
            <span className={styles.probBar} aria-hidden="true">
              <span
                className={styles.probFill}
                style={{ width: `${String(Math.round(f.probability * 100))}%` }}
              />
            </span>
          </div>
        )}
      </td>
      <td className={styles.num}>
        {remainingText(f.remaining_hours)}
        <p className={styles.meta}>Hedef: {formatDateTime(f.resolve_due_at)}</p>
      </td>
      <td className={styles.num}>{formatDateTime(f.expected_resolve_at)}</td>
      <td>
        <Badge tone={f.basis === 'breached' ? 'danger' : f.basis === 'none' ? 'neutral' : 'info'}>
          {FORECAST_BASIS_LABELS[f.basis] ?? 'Bilinmiyor'}
        </Badge>
        <p className={styles.meta}>
          {f.sample_resolved} çözülmüş + {f.sample_censored} açık olay
        </p>
      </td>
      <td>
        {reasons.length > 0 ? (
          <ul className={styles.reasons}>
            {reasons.map((r) => (
              <li key={r.code}>{r.message}</li>
            ))}
          </ul>
        ) : (
          '—'
        )}
      </td>
    </tr>
  );
}
