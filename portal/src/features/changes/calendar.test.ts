import { describe, expect, it } from 'vitest';
import {
  changesOnDay,
  conflictMap,
  freezeOnDay,
  shiftAnchor,
  visibleDays,
  windowConflicts,
} from './calendar';
import { makeChange, makeFreezeWindow } from './testUtils';

// Testler TZ=Europe/Istanbul ile koşar; günler yerel saatle hesaplanır
const local = (y: number, m: number, d: number, h = 0) => new Date(y, m - 1, d, h);

describe('takvim aralığı', () => {
  it('hafta pazartesi başlar, 7 gün', () => {
    const days = visibleDays('week', local(2026, 10, 15)); // Perşembe
    expect(days).toHaveLength(7);
    expect(days[0]).toEqual(local(2026, 10, 12));
    expect(days[6]).toEqual(local(2026, 10, 18));
  });

  it('ay tam haftalarla doldurulur', () => {
    const days = visibleDays('month', local(2026, 10, 15));
    expect(days[0]).toEqual(local(2026, 9, 28)); // 1 Ekim 2026 Perşembe
    expect(days.length % 7).toBe(0);
    expect(days.at(-1)).toEqual(local(2026, 11, 1));
  });

  it('ileri/geri', () => {
    expect(shiftAnchor('week', local(2026, 10, 15), 1)).toEqual(local(2026, 10, 22));
    expect(shiftAnchor('month', local(2026, 1, 31), 1)).toEqual(local(2026, 2, 1));
  });
});

describe('gün içerikleri', () => {
  it('pencereye değen değişiklikler ve dondurma günleri', () => {
    const c = makeChange({
      planned_start: local(2026, 10, 20, 22).toISOString(),
      planned_end: local(2026, 10, 21, 2).toISOString(),
    });
    expect(changesOnDay(local(2026, 10, 20), [c])).toEqual([c]);
    expect(changesOnDay(local(2026, 10, 21), [c])).toEqual([c]);
    expect(changesOnDay(local(2026, 10, 22), [c])).toEqual([]);
    const fw = makeFreezeWindow({
      start_at: local(2026, 10, 24).toISOString(),
      end_at: local(2026, 10, 25).toISOString(),
    });
    expect(freezeOnDay(local(2026, 10, 24), [fw])).toEqual([fw]);
    expect(freezeOnDay(local(2026, 10, 25), [fw])).toEqual([]); // bitiş hariç
  });
});

describe('çakışma işaretleri (sunucu kuralının özeti)', () => {
  const fw = makeFreezeWindow({
    start_at: '2026-10-20T00:00:00Z',
    end_at: '2026-10-21T00:00:00Z',
    reason: 'Ay sonu',
  });

  it('dondurma penceresi (acil hariç) ve aynı CI üzerinde çakışan değişiklik', () => {
    const a = makeChange({
      number: 'CHG-A',
      state: 'scheduled',
      planned_start: '2026-10-20T10:00:00Z',
      planned_end: '2026-10-20T12:00:00Z',
      affected_ci_ids: ['ci-1'],
    });
    const b = makeChange({
      number: 'CHG-B',
      state: 'scheduled',
      planned_start: '2026-10-20T11:00:00Z',
      planned_end: '2026-10-20T13:00:00Z',
      affected_ci_ids: ['ci-1', 'ci-2'],
    });
    const emergency = makeChange({
      number: 'CHG-E',
      change_type: 'emergency',
      planned_start: '2026-10-20T15:00:00Z',
      planned_end: '2026-10-20T16:00:00Z',
      affected_ci_ids: ['ci-9'],
    });
    const map = conflictMap([a, b, emergency], [fw]);
    expect(map.get(a.id)).toEqual(['Dondurma penceresi: Ay sonu', 'Aynı CI üzerinde CHG-B']);
    expect(map.get(b.id)).toEqual(['Dondurma penceresi: Ay sonu', 'Aynı CI üzerinde CHG-A']);
    expect(map.has(emergency.id)).toBe(false);
  });

  it('aynı CI çakışması yalnızca zamanlanmış/uygulanan değişikliklerle (sunucu gibi)', () => {
    const assess = makeChange({
      state: 'assess',
      planned_start: '2026-10-22T10:00:00Z',
      planned_end: '2026-10-22T12:00:00Z',
      affected_ci_ids: ['ci-1'],
    });
    const scheduled = makeChange({
      number: 'CHG-S',
      state: 'scheduled',
      planned_start: '2026-10-22T11:00:00Z',
      planned_end: '2026-10-22T13:00:00Z',
      affected_ci_ids: ['ci-1'],
    });
    const map = conflictMap([assess, scheduled], []);
    expect(map.get(assess.id)).toEqual(['Aynı CI üzerinde CHG-S']);
    expect(map.has(scheduled.id)).toBe(false);
  });

  it('kaydedilmemiş pencere için ön denetim', () => {
    const other = makeChange({
      number: 'CHG-X',
      state: 'scheduled',
      planned_start: '2026-10-22T10:00:00Z',
      planned_end: '2026-10-22T12:00:00Z',
      affected_ci_ids: ['ci-1'],
    });
    const result = windowConflicts(
      {
        start: new Date('2026-10-22T11:00:00Z'),
        end: new Date('2026-10-22T11:30:00Z'),
        ciIds: ['ci-1'],
        changeType: 'normal',
      },
      [other],
      [fw],
    );
    expect(result).toEqual(['Aynı CI üzerinde CHG-X']);
    expect(
      windowConflicts(
        {
          start: new Date('2026-10-20T05:00:00Z'),
          end: new Date('2026-10-20T06:00:00Z'),
          ciIds: [],
          changeType: 'normal',
        },
        [other],
        [fw],
      ),
    ).toEqual(['Dondurma penceresi: Ay sonu']);
  });
});
