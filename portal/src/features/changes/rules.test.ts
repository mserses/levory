import { describe, expect, it } from 'vitest';
import {
  allowedTransitions,
  canAssessRisk,
  isAwaitingMyVote,
  pirRequired,
  stepsFor,
  voteBlockReason,
} from './rules';
import { makeApproval, makeChange } from './testUtils';

const MANAGER = { username: 'mudur', roles: ['change_manager'] };
const CAB = { username: 'kurul1', roles: ['cab'] };
const REQUESTER_AGENT = { username: 'ayse', roles: ['agent'] };
const OTHER_AGENT = { username: 'mehmet', roles: ['agent'] };
const ADMIN = { username: 'yonetici', roles: ['admin'] };

describe('allowedTransitions (backend services/changes.py ile aynı kurallar)', () => {
  it('taslak: talep eden ve change_manager gönderebilir/iptal edebilir; başkası hiçbir şey', () => {
    const c = makeChange({ state: 'draft', requested_by: 'ayse' });
    expect(allowedTransitions(c, REQUESTER_AGENT)).toEqual(['assess', 'cancelled']);
    expect(allowedTransitions(c, MANAGER)).toEqual(['assess', 'cancelled']);
    expect(allowedTransitions(c, OTHER_AGENT)).toEqual([]);
    expect(allowedTransitions(c, CAB)).toEqual([]);
  });

  it('değerlendirme: normal CAB’ı atlayamaz, standart CAB’a gitmez', () => {
    const normal = makeChange({ state: 'assess', change_type: 'normal' });
    expect(allowedTransitions(normal, MANAGER)).toEqual(['draft', 'authorize', 'cancelled']);
    expect(allowedTransitions(normal, REQUESTER_AGENT)).toEqual(['draft', 'cancelled']);
    const standard = makeChange({ state: 'assess', change_type: 'standard' });
    expect(allowedTransitions(standard, MANAGER)).toEqual(['draft', 'scheduled', 'cancelled']);
    expect(allowedTransitions(standard, REQUESTER_AGENT)).toEqual([
      'draft',
      'scheduled',
      'cancelled',
    ]);
  });

  it('yetkilendirme/zamanlama yalnızca change_manager (admin dahil)', () => {
    const c = makeChange({ state: 'authorize' });
    expect(allowedTransitions(c, MANAGER)).toEqual(['scheduled', 'rejected', 'cancelled']);
    expect(allowedTransitions(c, ADMIN)).toEqual(['scheduled', 'rejected', 'cancelled']);
    expect(allowedTransitions(c, REQUESTER_AGENT)).toEqual([]);
    expect(allowedTransitions(c, CAB)).toEqual([]);
  });

  it('uygulama: uygulayıcı (assignee, yoksa talep eden) ya da change_manager', () => {
    const withAssignee = makeChange({ state: 'scheduled', assignee: 'mehmet' });
    expect(allowedTransitions(withAssignee, OTHER_AGENT)).toEqual(['implement']);
    expect(allowedTransitions(withAssignee, REQUESTER_AGENT)).toEqual([]);
    expect(allowedTransitions(withAssignee, MANAGER)).toEqual(['implement', 'cancelled']);
    const noAssignee = makeChange({ state: 'implement', assignee: null });
    expect(allowedTransitions(noAssignee, REQUESTER_AGENT)).toEqual(['review', 'failed']);
    expect(allowedTransitions(makeChange({ state: 'failed' }), REQUESTER_AGENT)).toEqual([
      'review',
    ]);
  });

  it('kapatma yalnızca change_manager; uç durumlarda geçiş yok', () => {
    expect(allowedTransitions(makeChange({ state: 'review' }), REQUESTER_AGENT)).toEqual([]);
    expect(allowedTransitions(makeChange({ state: 'review' }), MANAGER)).toEqual(['closed']);
    for (const state of ['closed', 'rejected', 'cancelled'] as const) {
      expect(allowedTransitions(makeChange({ state }), ADMIN)).toEqual([]);
    }
  });

  it('yapay zekâ aktörü hiçbir geçiş yapamaz', () => {
    const c = makeChange({ state: 'draft', requested_by: 'ai:change' });
    expect(allowedTransitions(c, { username: 'ai:change', roles: ['admin'] })).toEqual([]);
  });
});

describe('CAB oyu', () => {
  const authorize = makeChange({ state: 'authorize', requested_by: 'ayse', assignee: 'mehmet' });

  it('talep eden ya da uygulayıcı oy veremez (gerekçeli)', () => {
    expect(voteBlockReason(authorize, [], { username: 'ayse', roles: ['cab'] })).toMatch(
      /talep eden/i,
    );
    expect(voteBlockReason(authorize, [], { username: 'mehmet', roles: ['cab'] })).toMatch(
      /uygulayıcı/i,
    );
  });

  it('rol, durum ve önceki oy denetlenir', () => {
    expect(voteBlockReason(authorize, [], OTHER_AGENT)).toMatch(/CAB üyesi/);
    expect(voteBlockReason(makeChange({ state: 'assess' }), [], CAB)).toMatch(
      /yetkilendirme aşamasında/,
    );
    expect(voteBlockReason(authorize, [makeApproval({ approver: 'kurul1' })], CAB)).toMatch(
      /kararınızı verdiniz/,
    );
    expect(voteBlockReason(authorize, [], CAB)).toBeNull();
    expect(voteBlockReason(authorize, [], { username: 'ai:change', roles: ['cab'] })).toMatch(
      /yapay zekâ/i,
    );
  });

  it('oyumu bekleyenler', () => {
    expect(isAwaitingMyVote(authorize, [], CAB)).toBe(true);
    expect(isAwaitingMyVote(authorize, [makeApproval({ approver: 'kurul1' })], CAB)).toBe(false);
    expect(isAwaitingMyVote(authorize, [], { username: 'ayse', roles: ['cab'] })).toBe(false);
  });
});

describe('aşama göstergesi ve PIR', () => {
  it('normal yol CAB adımını içerir, standart içermez', () => {
    const normal = stepsFor(makeChange({ state: 'scheduled', change_type: 'normal' }));
    expect(normal.map((s) => s.state)).toEqual([
      'draft',
      'assess',
      'authorize',
      'scheduled',
      'implement',
      'review',
      'closed',
    ]);
    expect(normal.map((s) => s.status)).toEqual([
      'done',
      'done',
      'done',
      'current',
      'upcoming',
      'upcoming',
      'upcoming',
    ]);
    const standard = stepsFor(makeChange({ state: 'assess', change_type: 'standard' }));
    expect(standard.map((s) => s.state)).not.toContain('authorize');
  });

  it('başarısız uygulama yolda gösterilir', () => {
    const steps = stepsFor(
      makeChange({ state: 'review', change_type: 'normal', implementation_outcome: 'failed' }),
    );
    expect(steps.map((s) => s.state)).toContain('failed');
    expect(steps.find((s) => s.state === 'review')?.status).toBe('current');
  });

  it('PIR acil ya da başarısız değişiklikte zorunlu', () => {
    expect(pirRequired(makeChange({ change_type: 'emergency' }))).toBe(true);
    expect(pirRequired(makeChange({ implementation_outcome: 'failed' }))).toBe(true);
    expect(pirRequired(makeChange({ implementation_outcome: 'succeeded' }))).toBe(false);
  });

  it('risk değerlendirmesi yalnızca taslak/değerlendirmede ve agent/change_manager', () => {
    expect(canAssessRisk(makeChange({ state: 'draft' }), ['agent'])).toBe(true);
    expect(canAssessRisk(makeChange({ state: 'assess' }), ['change_manager'])).toBe(true);
    expect(canAssessRisk(makeChange({ state: 'authorize' }), ['change_manager'])).toBe(false);
    expect(canAssessRisk(makeChange({ state: 'draft' }), ['cab'])).toBe(false);
  });
});
