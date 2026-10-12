/**
 * Türkçe tarih/saat ve süre biçimlendirme. Ekranlar tarihleri YALNIZCA buradan biçimlendirir.
 * Saat dilimi `config.timeZone` (VITE_TIME_ZONE, varsayılan Europe/Istanbul).
 */
import { config } from '@/config/env';

export const LOCALE = 'tr-TR';

type DateInput = string | number | Date | null | undefined;

export function toDate(value: DateInput): Date | null {
  if (value === null || value === undefined || value === '') return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

const cache = new Map<string, Intl.DateTimeFormat>();
function dtf(options: Intl.DateTimeFormatOptions, timeZone = config.timeZone): Intl.DateTimeFormat {
  const key = JSON.stringify([options, timeZone]);
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(LOCALE, { ...options, timeZone });
    cache.set(key, f);
  }
  return f;
}

/** "12.10.2026 14:05" */
export function formatDateTime(value: DateInput, fallback = '—'): string {
  const d = toDate(value);
  if (!d) return fallback;
  return dtf({
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

/** "12 Ekim 2026" */
export function formatDate(value: DateInput, fallback = '—'): string {
  const d = toDate(value);
  if (!d) return fallback;
  return dtf({ day: 'numeric', month: 'long', year: 'numeric' }).format(d);
}

/** "14:05" */
export function formatTime(value: DateInput, fallback = '—'): string {
  const d = toDate(value);
  if (!d) return fallback;
  return dtf({ hour: '2-digit', minute: '2-digit' }).format(d);
}

const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' });

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
];

/** "5 dakika önce", "2 saat sonra", "dün", "şimdi" */
export function formatRelative(value: DateInput, now: Date = new Date(), fallback = '—'): string {
  const d = toDate(value);
  if (!d) return fallback;
  const diffSeconds = (d.getTime() - now.getTime()) / 1000;
  const abs = Math.abs(diffSeconds);
  if (abs < 45) return 'şimdi';
  for (const [unit, size] of UNITS) {
    if (abs >= size || unit === 'minute') {
      return rtf.format(Math.round(diffSeconds / size), unit);
    }
  }
  return rtf.format(Math.round(diffSeconds / 60), 'minute');
}

/**
 * Süre (saniye) -> "3 gün 4 sa", "2 sa 5 dk", "45 dk", "1 dk'dan az".
 * En fazla iki birim gösterilir; negatif değerlerin mutlak değeri alınır.
 */
export function formatDuration(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds)) return '—';
  const s = Math.abs(Math.round(totalSeconds));
  if (s < 60) return '1 dk’dan az';
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (days) parts.push(`${days} gün`);
  if (hours) parts.push(`${hours} sa`);
  if (minutes && !days) parts.push(`${minutes} dk`);
  return parts.slice(0, 2).join(' ');
}

/** 0.873 -> "%87" (Türkçe yüzde yazımı) */
export function formatPercent(ratio: number | null | undefined, fallback = '—'): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return fallback;
  return new Intl.NumberFormat(LOCALE, { style: 'percent', maximumFractionDigits: 0 }).format(
    ratio,
  );
}
