/**
 * KPI panosu yardımcıları (backend `services/kpi.py`, docs/faz4.md). Yüzde KPI'ları 0-100,
 * süreler saat, sayılar adet gelir. Zaman serileri aynı `end`e sabitlenmiş ardışık kısa
 * pencerelerle (`GET /kpi?days=<kova>&end=<kova sonu>`) çizilir — sunucuda ek uç gerekmez.
 */
import type { BreachForecast, KPIReport, KPIValue, Priority } from '@/api/types';
import type { Tone } from '@/components';
import { LOCALE } from '@/lib/format';

export type KpiUnit = KPIValue['unit'];

export const WINDOW_OPTIONS = [
  { value: '7', label: 'Son 7 gün' },
  { value: '30', label: 'Son 30 gün' },
  { value: '90', label: 'Son 90 gün' },
] as const;

/** Panoda gösterilen KPI'lar (sıra = kutucuk sırası) */
export const DASHBOARD_KPIS = [
  'mttr',
  'mtta',
  'sla_response_compliance',
  'sla_resolve_compliance',
  'backlog',
  'automation_rate',
  'ai_triage_acceptance',
  'change_success_rate',
  'fcr_rate',
  'reopen_rate',
  'emergency_change_share',
  'problem_known_error_lead_time',
] as const;

/** Zaman serisi grafiklerinde kullanılan KPI'lar (kova başına tek istek) */
export const SERIES_KPIS = [
  'mttr',
  'sla_response_compliance',
  'sla_resolve_compliance',
  'automation_rate',
  'ai_triage_acceptance',
  'change_success_rate',
] as const;

const DAY_MS = 86_400_000;

/** Pencere sonunu saate yuvarlar: aynı saat içindeki yeniden çizimler aynı sorgu anahtarını kullanır */
export function anchorEnd(now: Date): string {
  const d = new Date(now.getTime());
  d.setUTCMinutes(0, 0, 0);
  return d.toISOString();
}

export interface Bucket {
  /** Kova sonu (hariç), ISO */
  end: string;
  /** Kova uzunluğu (gün) */
  days: number;
}

/** Pencereyi en eskiden en yeniye 6-7 eşit kovaya böler (kova ≥ 1 gün) */
export function bucketPlan(days: number, end: string): Bucket[] {
  const size = days <= 7 ? 1 : Math.max(1, Math.round(days / 6));
  const count = Math.max(1, Math.round(days / size));
  const endMs = new Date(end).getTime();
  return Array.from({ length: count }, (_, i) => ({
    end: new Date(endMs - (count - 1 - i) * size * DAY_MS).toISOString(),
    days: size,
  }));
}

const number = (digits: number) =>
  new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits, minimumFractionDigits: 0 });

/** KPI değeri gösterimi: "6,5 sa", "%87,5", "12" */
export function formatKpiValue(value: number | null | undefined, unit: KpiUnit): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (unit === 'percent') return `%${number(1).format(value)}`;
  if (unit === 'hours') return `${number(1).format(value)} sa`;
  return number(0).format(value);
}

export const TREND_LABELS: Record<KPIValue['trend'], string> = {
  improved: 'İyileşti',
  worsened: 'Kötüleşti',
  flat: 'Değişmedi',
  'n/a': 'Karşılaştırma yok',
};

export function trendTone(kpi: KPIValue): Tone {
  if (kpi.trend === 'improved') return 'success';
  if (kpi.trend === 'worsened') return 'danger';
  return 'neutral';
}

/** "▼ %40 önceki döneme göre (İyileşti)" — yön işareti + metin; renk tek başına anlam taşımaz */
export function deltaText(kpi: KPIValue): string {
  if (kpi.trend === 'n/a' || kpi.previous === null || kpi.delta === null) {
    return 'Önceki dönemde veri yok';
  }
  if (kpi.delta === 0) return 'Önceki dönemle aynı';
  const arrow = kpi.delta < 0 ? '▼' : '▲';
  const amount =
    kpi.delta_pct !== null
      ? `%${number(1).format(Math.abs(kpi.delta_pct))}`
      : formatKpiValue(Math.abs(kpi.delta), kpi.unit);
  return `${arrow} ${amount} önceki döneme göre (${TREND_LABELS[kpi.trend]})`;
}

export function findKpi(report: KPIReport | undefined, key: string): KPIValue | undefined {
  return report?.kpis.find((k) => k.key === key);
}

/** Kovalardaki tek KPI değeri (yüklenmemiş kova / veri yok = null) */
export function kpiSeries(reports: readonly (KPIReport | undefined)[], key: string) {
  return reports.map((r) => findKpi(r, key)?.value ?? null);
}

export interface Series {
  key: string;
  label: string;
  values: (number | null)[];
}

/** Kırılım boyutundan (ör. öncelik) seri listesi; anahtarlar sıralı, boş anahtar atlanır */
export function breakdownSeries(
  reports: readonly (KPIReport | undefined)[],
  key: string,
  dim: string,
): Series[] {
  const keys = new Set<string>();
  for (const r of reports) {
    for (const g of findKpi(r, key)?.breakdown?.[dim] ?? []) if (g.key) keys.add(g.key);
  }
  return [...keys].sort().map((k) => ({
    key: k,
    label: k,
    values: reports.map(
      (r) => findKpi(r, key)?.breakdown?.[dim]?.find((g) => g.key === k)?.value ?? null,
    ),
  }));
}

/** Kova etiketi: kovanın son günü ("12 Eki") */
export function bucketLabel(bucket: Bucket, timeZone: string): string {
  const lastDay = new Date(new Date(bucket.end).getTime() - 1);
  return new Intl.DateTimeFormat(LOCALE, { day: 'numeric', month: 'short', timeZone }).format(
    lastDay,
  );
}

/** Birikim yaşlanma kovaları (backend `AGING_BUCKETS`) */
export const AGING_LABELS: Record<string, string> = {
  '0-1g': '1 günden az',
  '1-3g': '1-3 gün',
  '3-7g': '3-7 gün',
  '7-30g': '7-30 gün',
  '30g+': '30 günden fazla',
};

export function agingItems(kpi: KPIValue | undefined): { label: string; value: number }[] {
  const raw = kpi?.details?.aging;
  const aging = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : {};
  return Object.keys(AGING_LABELS).map((k) => ({
    label: AGING_LABELS[k] ?? k,
    value: typeof aging[k] === 'number' ? aging[k] : 0,
  }));
}

/** Olasılığa göre azalan; tahminsizler (null) sonda — sunucu sırasıyla aynı, istemcide güvence */
export function rankForecasts(items: readonly BreachForecast[]): BreachForecast[] {
  return [...items].sort((a, b) => (b.probability ?? -1) - (a.probability ?? -1));
}

export const PRIORITY_ORDER: readonly string[] = ['P1', 'P2', 'P3', 'P4', 'P5'];
/** Sunucu öncelik alanı bazı şemalarda düz `string`; rozet yalnızca bilinen değerde */
export const isPriority = (v: string | null | undefined): v is Priority =>
  typeof v === 'string' && PRIORITY_ORDER.includes(v);
