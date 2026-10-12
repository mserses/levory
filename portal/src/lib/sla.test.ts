import { describe, expect, it } from 'vitest';
import { makeIncident } from '@/test/fixtures';
import { computeSla } from './sla';

const base = makeIncident({
  created_at: '2026-10-12T08:00:00Z',
  response_due_at: '2026-10-12T12:00:00Z',
  resolve_due_at: '2026-10-13T08:00:00Z',
});

describe('computeSla', () => {
  it('yanıt verilmemişse yanıt vadesi izlenir', () => {
    const s = computeSla(base, new Date('2026-10-12T09:00:00Z'));
    expect(s).toMatchObject({ state: 'on_track', metric: 'response', remainingSeconds: 3 * 3600 });
  });

  it('%75 eşiğinde risk, vade geçince ihlal', () => {
    expect(computeSla(base, new Date('2026-10-12T11:00:00Z')).state).toBe('at_risk');
    const b = computeSla(base, new Date('2026-10-12T12:30:00Z'));
    expect(b.state).toBe('breached');
    expect(b.remainingSeconds).toBe(-1800);
  });

  it('yanıt verildiyse çözüm vadesi', () => {
    const s = computeSla(
      { ...base, responded_at: '2026-10-12T08:30:00Z' },
      new Date('2026-10-12T09:00:00Z'),
    );
    expect(s.metric).toBe('resolve');
  });

  it('duraklatılmış SLA', () => {
    const s = computeSla(
      { ...base, status: 'on_hold', sla_paused_at: '2026-10-12T08:30:00Z' },
      new Date('2026-10-12T20:00:00Z'),
    );
    expect(s).toMatchObject({ state: 'paused', remainingSeconds: null });
  });

  it('çözülen kayıt: karşılandı / kaçırıldı; iptal: yok', () => {
    expect(
      computeSla({ ...base, status: 'resolved', resolved_at: '2026-10-12T20:00:00Z' }).state,
    ).toBe('met');
    expect(
      computeSla({ ...base, status: 'closed', resolved_at: '2026-10-14T20:00:00Z' }).state,
    ).toBe('missed');
    expect(computeSla({ ...base, status: 'cancelled' }).state).toBe('none');
    expect(computeSla({ ...base, response_due_at: null, resolve_due_at: null }).state).toBe('none');
  });
});
