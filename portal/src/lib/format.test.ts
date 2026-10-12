import { describe, expect, it } from 'vitest';
import {
  formatDate,
  formatDateTime,
  formatDuration,
  formatPercent,
  formatRelative,
  formatTime,
} from './format';

const NOW = new Date('2026-10-12T09:00:00Z'); // İstanbul 12:00

describe('tarih biçimleri (Europe/Istanbul)', () => {
  it('tarih-saat, tarih ve saat', () => {
    expect(formatDateTime('2026-10-12T09:05:00Z')).toBe('12.10.2026 12:05');
    expect(formatDate('2026-10-12T09:05:00Z')).toBe('12 Ekim 2026');
    expect(formatTime('2026-10-12T21:30:00Z')).toBe('00:30');
  });

  it('boş/geçersiz değerde yedek metin', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('geçersiz')).toBe('—');
    expect(formatDate(undefined, '-')).toBe('-');
  });
});

describe('formatRelative', () => {
  it('Türkçe göreli zaman', () => {
    expect(formatRelative('2026-10-12T08:59:50Z', NOW)).toBe('şimdi');
    expect(formatRelative('2026-10-12T08:55:00Z', NOW)).toBe('5 dakika önce');
    expect(formatRelative('2026-10-12T11:00:00Z', NOW)).toBe('2 saat sonra');
    expect(formatRelative('2026-10-11T09:00:00Z', NOW)).toBe('dün');
    expect(formatRelative('2026-09-28T09:00:00Z', NOW)).toBe('2 hafta önce');
    expect(formatRelative(null, NOW)).toBe('—');
  });
});

describe('formatDuration', () => {
  it('en fazla iki birim', () => {
    expect(formatDuration(30)).toBe('1 dk’dan az');
    expect(formatDuration(45 * 60)).toBe('45 dk');
    expect(formatDuration(2 * 3600 + 5 * 60)).toBe('2 sa 5 dk');
    expect(formatDuration(3 * 86400 + 4 * 3600 + 59)).toBe('3 gün 4 sa');
    expect(formatDuration(-3600)).toBe('1 sa');
    expect(formatDuration(Number.NaN)).toBe('—');
  });
});

describe('formatPercent', () => {
  it('Türkçe yüzde', () => {
    expect(formatPercent(0.873)).toBe('%87');
    expect(formatPercent(null)).toBe('—');
  });
});
