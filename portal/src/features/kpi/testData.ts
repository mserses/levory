import type { KPIReport, KPIValue } from '@/api/types';

export function makeKpi(over: Partial<KPIValue> & Pick<KPIValue, 'key'>): KPIValue {
  return {
    name: over.key,
    unit: 'percent',
    better: 'higher',
    value: 50,
    count: 10,
    previous: 50,
    previous_count: 10,
    delta: 0,
    delta_pct: 0,
    trend: 'flat',
    details: {},
    breakdown: {},
    ...over,
  };
}

/** Tüm pano KPI'larını içeren rapor; `scale` kova raporlarını birbirinden ayırmak için */
export function makeKpiReport(end = '2026-10-12T09:00:00Z', scale = 1): KPIReport {
  return {
    generated_at: end,
    window: { start: '2026-09-12T09:00:00Z', end },
    previous_window: { start: '2026-08-13T09:00:00Z', end: '2026-09-12T09:00:00Z' },
    kpis: [
      makeKpi({
        key: 'mttr',
        name: 'Ortalama çözüm süresi (MTTR)',
        unit: 'hours',
        better: 'lower',
        value: 6 * scale,
        previous: 10,
        delta: -4,
        delta_pct: -40,
        trend: 'improved',
        count: 42,
        breakdown: {
          priority: [
            { key: 'P1', value: 2 * scale, count: 3 },
            { key: 'P3', value: 8 * scale, count: 20 },
          ],
        },
      }),
      makeKpi({
        key: 'mtta',
        name: 'Ortalama yanıt süresi (MTTA)',
        unit: 'hours',
        better: 'lower',
        value: 0.5,
        previous: 0.4,
        delta: 0.1,
        delta_pct: 25,
        trend: 'worsened',
      }),
      makeKpi({ key: 'sla_response_compliance', name: 'Yanıt SLA uyumu', value: 92 }),
      makeKpi({ key: 'sla_resolve_compliance', name: 'Çözüm SLA uyumu', value: 81.5 }),
      makeKpi({
        key: 'backlog',
        name: 'Birikim',
        unit: 'count',
        better: 'lower',
        value: 17,
        details: { aging: { '0-1g': 5, '1-3g': 6, '3-7g': 3, '7-30g': 2, '30g+': 1 } },
      }),
      makeKpi({ key: 'automation_rate', name: 'İnsansız karşılama oranı', value: 64 * scale }),
      makeKpi({ key: 'ai_triage_acceptance', name: 'Yapay zekâ triage kabulü', value: 77 }),
      makeKpi({ key: 'change_success_rate', name: 'Değişiklik başarı oranı', value: 95 }),
      makeKpi({
        key: 'fcr_rate',
        name: 'İlk temasta çözüm',
        value: null,
        previous: null,
        delta: null,
        delta_pct: null,
        trend: 'n/a',
        count: 0,
      }),
    ],
  };
}
