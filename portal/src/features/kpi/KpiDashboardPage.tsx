import { useQueries, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { kpiApi, queryKeys, type KPIReport, type KPIReportQuery, type KPIValue } from '@/api';
import { Badge, ErrorState, Select, Spinner } from '@/components';
import { config } from '@/config/env';
import { formatDateTime } from '@/lib/format';
import { PRIORITY_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { BarChart } from './charts/BarChart';
import { LineChart, type LineSeries } from './charts/LineChart';
import {
  DASHBOARD_KPIS,
  SERIES_KPIS,
  WINDOW_OPTIONS,
  agingItems,
  anchorEnd,
  breakdownSeries,
  bucketLabel,
  bucketPlan,
  deltaText,
  findKpi,
  formatKpiValue,
  isPriority,
  PRIORITY_ORDER,
  kpiSeries,
  trendTone,
} from './model';
import styles from './Kpi.module.css';

/** /panolar — KPI panosu (sahibi: kpi). GET /kpi (ana pencere + zaman serisi kovaları) */
export default function KpiDashboardPage() {
  usePageTitle('Panolar');
  const [days, setDays] = useState('30');
  // Pencere sonu açılışta sabitlenir (saate yuvarlı): kutucuklar ve grafikler aynı ana bakar
  const [end] = useState(() => anchorEnd(new Date()));
  const mainQuery: KPIReportQuery = { days: Number(days), end, kpi: [...DASHBOARD_KPIS] };
  const report = useQuery({
    queryKey: queryKeys.kpi.report(mainQuery),
    queryFn: ({ signal }) => kpiApi.report(mainQuery, signal),
  });

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Panolar</h1>
          <p className="muted" style={{ margin: 0 }}>
            Sürekli iyileştirme göstergeleri: her değer aynı uzunluktaki önceki dönemle
            karşılaştırılır.
          </p>
        </div>
      </header>

      <div className={styles.toolbar}>
        <Select
          label="Dönem"
          options={WINDOW_OPTIONS}
          value={days}
          onChange={(e) => {
            setDays(e.target.value);
          }}
        />
        {report.data && (
          <p className={styles.windowNote}>
            {formatDateTime(report.data.window.start)} – {formatDateTime(report.data.window.end)}{' '}
            (önceki: {formatDateTime(report.data.previous_window.start)} –{' '}
            {formatDateTime(report.data.previous_window.end)})
          </p>
        )}
      </div>

      {report.isPending ? (
        <Spinner block label="KPI'lar yükleniyor…" showLabel />
      ) : report.isError ? (
        <ErrorState
          error={report.error}
          title="KPI raporu yüklenemedi"
          onRetry={() => void report.refetch()}
        />
      ) : (
        <>
          <section aria-labelledby="kpi-ozet" className="stack">
            <h2 id="kpi-ozet" className={styles.sectionHeading}>
              Özet
            </h2>
            <ul className={styles.tiles}>
              {DASHBOARD_KPIS.map((key) => {
                const kpi = findKpi(report.data, key);
                return kpi ? (
                  <li key={key}>
                    <KpiTile kpi={kpi} />
                  </li>
                ) : null;
              })}
            </ul>
          </section>
          <Charts days={Number(days)} end={end} report={report.data} />
        </>
      )}
    </div>
  );
}

function KpiTile({ kpi }: { kpi: KPIValue }) {
  const id = `kpi-${kpi.key}`;
  return (
    <article className={styles.tile} aria-labelledby={id}>
      <h3 id={id} className={styles.tileName}>
        {kpi.name}
      </h3>
      <p className={styles.tileValue}>{formatKpiValue(kpi.value, kpi.unit)}</p>
      <div>
        <Badge tone={trendTone(kpi)}>{deltaText(kpi)}</Badge>
      </div>
      <p className={styles.tileMeta}>
        Örneklem: {kpi.count} kayıt · önceki dönem {formatKpiValue(kpi.previous, kpi.unit)} (
        {kpi.previous_count} kayıt)
      </p>
    </article>
  );
}

function Charts({ days, end, report }: { days: number; end: string; report: KPIReport }) {
  const plan = bucketPlan(days, end);
  const results = useQueries({
    queries: plan.map((b) => {
      const q: KPIReportQuery = { days: b.days, end: b.end, kpi: [...SERIES_KPIS] };
      return {
        queryKey: queryKeys.kpi.report(q),
        queryFn: ({ signal }: { signal: AbortSignal }) => kpiApi.report(q, signal),
      };
    }),
  });
  const reports = results.map((r) => r.data);
  const loading = results.some((r) => r.isPending);
  const failed = results.find((r) => r.isError);
  const xLabels = plan.map((b) => bucketLabel(b, config.timeZone));
  const bucketNote = `Her nokta ${String(plan[0]?.days ?? 1)} günlük dönem.`;

  const mttrSeries: LineSeries[] = breakdownSeries(reports, 'mttr', 'priority')
    .filter((s) => isPriority(s.key))
    .map((s) => ({
      ...s,
      label: isPriority(s.key) ? PRIORITY_LABELS[s.key] : s.key,
      colorIndex: PRIORITY_ORDER.indexOf(s.key),
    }));
  const sla: LineSeries[] = [
    {
      key: 'response',
      label: 'Yanıt',
      values: kpiSeries(reports, 'sla_response_compliance'),
      colorIndex: 0,
    },
    {
      key: 'resolve',
      label: 'Çözüm',
      values: kpiSeries(reports, 'sla_resolve_compliance'),
      colorIndex: 1,
    },
  ];
  const rates: LineSeries[] = [
    {
      key: 'automation',
      label: 'İnsansız karşılama',
      values: kpiSeries(reports, 'automation_rate'),
      colorIndex: 0,
    },
    {
      key: 'ai',
      label: 'Yapay zekâ triage kabulü',
      values: kpiSeries(reports, 'ai_triage_acceptance'),
      colorIndex: 1,
    },
    {
      key: 'change',
      label: 'Değişiklik başarısı',
      values: kpiSeries(reports, 'change_success_rate'),
      colorIndex: 2,
    },
  ];

  return (
    <section aria-labelledby="kpi-grafikler" className="stack">
      <h2 id="kpi-grafikler" className={styles.sectionHeading}>
        Zaman içinde
      </h2>
      {failed?.isError && <ErrorState error={failed.error} title="Zaman serisi yüklenemedi" />}
      {loading && <Spinner label="Grafikler yükleniyor…" showLabel />}
      <div className={styles.charts}>
        <div className="card">
          <LineChart
            title="MTTR — önceliğe göre"
            description={`Duraklatma hariç ortalama çözüm süresi. ${bucketNote}`}
            unit="hours"
            xLabels={xLabels}
            series={mttrSeries}
          />
        </div>
        <div className="card">
          <LineChart
            title="SLA uyumu"
            description={`Hedef süresinde yanıtlanan / çözülen olay oranı. ${bucketNote}`}
            unit="percent"
            xLabels={xLabels}
            series={sla}
          />
        </div>
        <div className="card">
          <BarChart
            title="Birikim yaşlanması"
            description="Dönem sonunda açık olayların yaşı."
            unit="count"
            items={agingItems(findKpi(report, 'backlog'))}
          />
        </div>
        <div className="card">
          <LineChart
            title="Otomasyon, yapay zekâ kabulü, değişiklik başarısı"
            description={`İnsansız karşılanan talep, düzeltilmeden kabul edilen triage ve başarılı değişiklik oranı. ${bucketNote}`}
            unit="percent"
            xLabels={xLabels}
            series={rates}
          />
        </div>
      </div>
    </section>
  );
}
