import { describe, expect, it } from 'vitest';
import {
  availableSteps,
  evidenceValue,
  measureDueAt,
  parseMeasurement,
  stepNote,
  timeline,
} from './model';
import { makeImprovement } from './testData';

describe('CSI modeli', () => {
  it('adımlar duruma ve role göre (onay/red/kapatma yalnızca approver)', () => {
    const idea = makeImprovement({ state: 'idea' });
    expect(availableSteps(idea, ['agent'])).toEqual([]);
    expect(availableSteps(idea, ['approver'])).toEqual(['approve', 'reject']);
    const approved = makeImprovement({ state: 'approved' });
    expect(availableSteps(approved, ['agent'])).toEqual(['start']);
    expect(availableSteps(approved, ['approver'])).toEqual(['start', 'reject']);
    // in_progress + KPI: önce uygulandı işareti, sonra ölçüm
    const running = makeImprovement({ state: 'in_progress' });
    expect(availableSteps(running, ['agent'])).toEqual(['implemented']);
    const implemented = makeImprovement({ implemented_at: '2026-09-10T09:00:00Z' });
    expect(availableSteps(implemented, ['agent'])).toEqual(['measure']);
    expect(availableSteps(implemented, ['admin'])).toEqual(['measure', 'reject']);
    // KPI'sız kayıt ölçülmez, notla kapanır
    const noKpi = makeImprovement({
      kpi_key: null,
      kpi_dimension: null,
      kpi_dimension_value: null,
    });
    expect(availableSteps(noKpi, ['agent'])).toEqual([]);
    expect(availableSteps(noKpi, ['approver'])).toEqual(['close', 'reject']);
    const measured = makeImprovement({ state: 'measured' });
    expect(availableSteps(measured, ['agent'])).toEqual(['measure']);
    expect(availableSteps(measured, ['approver'])).toEqual(['close', 'measure']);
    expect(availableSteps(makeImprovement({ state: 'closed' }), ['admin'])).toEqual([]);
    expect(availableSteps(makeImprovement({ state: 'rejected' }), ['admin'])).toEqual([]);
    expect(availableSteps(idea, ['requester'])).toEqual([]);
  });

  it('not gereksinimi: red zorunlu, KPI’sız kapanış zorunlu', () => {
    const imp = makeImprovement();
    expect(stepNote('reject', imp)).toBe('required');
    expect(stepNote('close', makeImprovement({ state: 'measured' }))).toBe('optional');
    expect(stepNote('close', makeImprovement({ kpi_key: null }))).toBe('required');
    expect(stepNote('approve', imp)).toBe('optional');
    expect(stepNote('measure', imp)).toBe('none');
  });

  it('ölçüm penceresi, zaman çizelgesi, kanıt metni, ölçüm JSON’u', () => {
    expect(measureDueAt(makeImprovement())).toBeNull();
    expect(
      measureDueAt(makeImprovement({ implemented_at: '2026-09-10T09:00:00Z' }))?.toISOString(),
    ).toBe('2026-10-10T09:00:00.000Z');
    expect(timeline(makeImprovement()).map((e) => e.label)).toEqual([
      'Önerildi',
      'Onaylandı',
      'Uygulamaya başlandı',
    ]);
    expect(evidenceValue(['INC1', 'INC2'])).toBe('INC1, INC2');
    expect(evidenceValue({ a: 1 })).toBe('{"a":1}');
    expect(evidenceValue('<b>x</b>')).toBe('<b>x</b>');
    expect(parseMeasurement(makeImprovement())).toBeNull();
    const m = parseMeasurement(
      makeImprovement({
        measurement: {
          before: { value: 10, window: { start: 'a', end: 'b' } },
          after: { value: 5, count: 6, window: { start: 'c', end: 'd' } },
          delta_pct: -50,
          improved: true,
        },
      }),
    );
    expect(m).toEqual({
      before: { value: 10, window: { start: 'a', end: 'b' } },
      after: { value: 5, count: 6, window: { start: 'c', end: 'd' } },
      deltaPct: -50,
      improved: true,
    });
  });
});
