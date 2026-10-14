import { describe, expect, it } from 'vitest';
import type { KPIReport, KPIValue } from '@/api/types';
import {
  anchorEnd,
  bucketPlan,
  breakdownSeries,
  deltaText,
  formatKpiValue,
  kpiSeries,
  trendTone,
} from './model';

const kpi = (over: Partial<KPIValue> = {}): KPIValue => ({
  key: 'mttr',
  name: 'Ortalama çözüm süresi (MTTR)',
  unit: 'hours',
  better: 'lower',
  value: 6,
  count: 10,
  previous: 10,
  previous_count: 8,
  delta: -4,
  delta_pct: -40,
  trend: 'improved',
  details: {},
  breakdown: {},
  ...over,
});

const report = (kpis: KPIValue[], end: string): KPIReport => ({
  generated_at: end,
  window: { start: end, end },
  previous_window: { start: end, end },
  kpis,
});

describe('KPI modeli', () => {
  it('pencere sonu saate yuvarlanır (önbellek anahtarı kararlı)', () => {
    expect(anchorEnd(new Date('2026-10-12T09:41:13.500Z'))).toBe('2026-10-12T09:00:00.000Z');
  });

  it('kova planı: 30 gün → 6 × 5 gün, 7 gün → 7 × 1 gün; en eski önce', () => {
    const plan = bucketPlan(30, '2026-10-12T09:00:00.000Z');
    expect(plan).toHaveLength(6);
    expect(plan.every((b) => b.days === 5)).toBe(true);
    expect(plan.at(-1)?.end).toBe('2026-10-12T09:00:00.000Z');
    expect(plan[0]?.end).toBe('2026-09-17T09:00:00.000Z');
    const week = bucketPlan(7, '2026-10-12T09:00:00.000Z');
    expect(week).toHaveLength(7);
    expect(week.every((b) => b.days === 1)).toBe(true);
    expect(bucketPlan(90, '2026-10-12T09:00:00.000Z').map((b) => b.days)).toEqual(
      Array<number>(6).fill(15),
    );
  });

  it('değer biçimi birime göre (yüzde 0-100 gelir)', () => {
    expect(formatKpiValue(6.5, 'hours')).toBe('6,5 sa');
    expect(formatKpiValue(87.456, 'percent')).toBe('%87,5');
    expect(formatKpiValue(12, 'count')).toBe('12');
    expect(formatKpiValue(null, 'hours')).toBe('—');
  });

  it('eğilim tonu ve değişim metni (renk tek başına değil: metin + işaret)', () => {
    expect(trendTone(kpi())).toBe('success');
    expect(deltaText(kpi())).toBe('▼ %40 önceki döneme göre (İyileşti)');
    expect(trendTone(kpi({ trend: 'worsened' }))).toBe('danger');
    expect(deltaText(kpi({ delta_pct: 12.5, delta: 1, trend: 'worsened' }))).toBe(
      '▲ %12,5 önceki döneme göre (Kötüleşti)',
    );
    expect(deltaText(kpi({ trend: 'n/a', delta_pct: null, previous: null }))).toBe(
      'Önceki dönemde veri yok',
    );
    // önceki değer 0 → yüzde yok, mutlak fark gösterilir
    expect(
      deltaText(kpi({ unit: 'count', delta: 3, delta_pct: null, previous: 0, trend: 'worsened' })),
    ).toBe('▲ 3 önceki döneme göre (Kötüleşti)');
    expect(deltaText(kpi({ delta: 0, delta_pct: 0, trend: 'flat' }))).toBe('Önceki dönemle aynı');
  });

  it('zaman serisi: kovalardan KPI değeri ve kırılım serisi (eksik değer null)', () => {
    const r1 = report(
      [
        kpi({
          value: 8,
          breakdown: {
            priority: [
              { key: 'P1', value: 2, count: 1 },
              { key: 'P3', value: 9, count: 3 },
            ],
          },
        }),
      ],
      '2026-10-07T09:00:00Z',
    );
    const r2 = report(
      [kpi({ value: 5, breakdown: { priority: [{ key: 'P3', value: 6, count: 2 }] } })],
      '2026-10-12T09:00:00Z',
    );
    expect(kpiSeries([r1, r2], 'mttr')).toEqual([8, 5]);
    expect(kpiSeries([r1, undefined], 'mttr')).toEqual([8, null]);
    const series = breakdownSeries([r1, r2], 'mttr', 'priority');
    expect(series.map((s) => s.key)).toEqual(['P1', 'P3']);
    expect(series[0]?.values).toEqual([2, null]);
    expect(series[1]?.values).toEqual([9, 6]);
  });
});
