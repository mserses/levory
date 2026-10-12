import { describe, expect, it } from 'vitest';
import { makeDecision, makeIncident } from '@/test/fixtures';
import { diffDraft } from './components/IncidentEditForm';
import { buildDiff, displayValue, parseDecisionOutput } from './decisionOutput';
import {
  PAGE_SIZE,
  hasActiveFilters,
  parseQueueParams,
  toApiQuery,
  toSearchParams,
} from './queueParams';
import { sortBySlaDue } from './queueSort';
import {
  ALLOWED_TRANSITIONS,
  calculatePriority,
  isFinalStatus,
  isUrgentPriorityChange,
  pausesSla,
} from './rules';

describe('queueParams', () => {
  it('URL ↔ filtre gidiş-dönüş; geçersiz değerler yok sayılır', () => {
    const f = parseQueueParams(
      new URLSearchParams('durum=on_hold&oncelik=P2&atanan=ayse&benim=1&sirala=yeni&sayfa=3'),
    );
    expect(f).toEqual({
      status: 'on_hold',
      priority: 'P2',
      assignee: 'ayse',
      mine: true,
      sort: 'newest',
      page: 3,
    });
    expect(parseQueueParams(toSearchParams(f))).toEqual(f);
    const bad = parseQueueParams(new URLSearchParams('durum=xx&oncelik=P9&sayfa=-2'));
    expect(bad.status).toBeNull();
    expect(bad.priority).toBeNull();
    expect(bad.page).toBe(1);
    expect(hasActiveFilters(bad)).toBe(false);
    expect(hasActiveFilters(f)).toBe(true);
    expect(toSearchParams(bad).toString()).toBe('');
  });

  it('API sorgusu: bir fazla kayıt ister, offset sayfadan hesaplanır', () => {
    const q = toApiQuery(parseQueueParams(new URLSearchParams('sayfa=2&benim=1')));
    expect(q).toEqual({ limit: PAGE_SIZE + 1, offset: PAGE_SIZE, mine: true });
  });
});

describe('sortBySlaDue', () => {
  it('ihlal/en yakın vade önce, duraklatılmış sonra, SLA yok en sonda', () => {
    const now = new Date('2026-10-12T12:00:00Z');
    const base = { created_at: '2026-10-12T08:00:00Z', responded_at: '2026-10-12T08:10:00Z' };
    const later = makeIncident({
      ...base,
      number: 'LATER',
      resolve_due_at: '2026-10-13T12:00:00Z',
    });
    const breached = makeIncident({
      ...base,
      number: 'BREACHED',
      resolve_due_at: '2026-10-12T11:00:00Z',
    });
    const paused = makeIncident({
      ...base,
      number: 'PAUSED',
      status: 'on_hold',
      resolve_due_at: '2026-10-12T12:30:00Z',
      sla_paused_at: '2026-10-12T10:00:00Z',
    });
    const none = makeIncident({ number: 'NONE', response_due_at: null, resolve_due_at: null });
    const soon = makeIncident({ ...base, number: 'SOON', resolve_due_at: '2026-10-12T13:00:00Z' });
    expect(sortBySlaDue([none, paused, later, soon, breached], now).map((i) => i.number)).toEqual([
      'BREACHED',
      'SOON',
      'LATER',
      'PAUSED',
      'NONE',
    ]);
  });
});

describe('rules', () => {
  it('geçişler, kapalı durum, SLA duraklatma, öncelik matrisi', () => {
    expect(ALLOWED_TRANSITIONS.resolved).toEqual(['in_progress', 'closed']);
    expect(isFinalStatus('closed')).toBe(true);
    expect(isFinalStatus('new')).toBe(false);
    expect(pausesSla('awaiting_caller')).toBe(true);
    expect(pausesSla('awaiting_vendor')).toBe(true);
    expect(pausesSla('internal')).toBe(false);
    expect(pausesSla(null)).toBe(false);
    expect(calculatePriority('high', 'high')).toBe('P1');
    expect(calculatePriority('low', 'low')).toBe('P5');
    expect(calculatePriority('medium', 'high')).toBe('P2');
    expect(isUrgentPriorityChange('P3', 'P2')).toBe(true);
    expect(isUrgentPriorityChange('P2', 'P4')).toBe(true);
    expect(isUrgentPriorityChange('P3', 'P4')).toBe(false);
  });
});

describe('decisionOutput', () => {
  it('çıktıyı güvenle ayrıştırır ve öneri farkını üretir', () => {
    const parsed = parseDecisionOutput({
      proposed: { category: 'ag', impact: 'medium', urgency: 'high', unknown: 'x', service: 5 },
      applied_fields: ['category'],
      pending_fields: ['impact', 'urgency'],
      dropped_kb_ids: ['KB999'],
      off_taxonomy: { service: 'uydu', bad: 3 },
      current_priority: 'P4',
      proposed_priority: 'P2',
      policy_reason: 'P4 -> P2 yükseltme onay gerektirir.',
      suggested_resolution: 'Sertifikayı yenileyin.',
    });
    expect(parsed.proposed).toEqual({ category: 'ag', impact: 'medium', urgency: 'high' });
    expect(parsed.offTaxonomy).toEqual([{ field: 'service', value: 'uydu' }]);
    expect(parsed.droppedKbIds).toEqual(['KB999']);
    const incident = makeIncident({
      category: 'ag',
      impact: 'low',
      urgency: 'medium',
      priority: 'P4',
    });
    const diff = buildDiff(parsed, incident);
    expect(diff.map((r) => [r.field, r.state])).toEqual([
      ['category', 'applied'],
      ['impact', 'pending'],
      ['urgency', 'pending'],
      ['priority', 'pending'],
    ]);
  });

  it('bozuk/boş çıktı ve hata kodu', () => {
    const parsed = parseDecisionOutput({
      error: 'invalid_output',
      proposed: 'x',
      applied_fields: 'y',
    });
    expect(parsed.proposed).toEqual({});
    expect(parsed.appliedFields).toEqual([]);
    expect(parsed.error).toMatch(/şemaya uymadı/);
    expect(buildDiff(parsed, makeIncident())).toEqual([]);
    expect(displayValue('impact', 'high')).toMatch(/^Yüksek/);
    expect(displayValue('impact', 'bilinmeyen')).toBe('bilinmeyen');
    expect(displayValue('category', null)).toBe('—');
    expect(makeDecision().output).toBeDefined();
  });
});

describe('diffDraft', () => {
  it('yalnızca değişen alanları gönderir; boş isteğe bağlı alan null olur', () => {
    const base = makeIncident({ category: 'ag', assignee: 'ayse' });
    const patch = diffDraft(base, {
      title: base.title,
      description: base.description,
      kind: base.kind,
      caller: base.caller,
      category: '',
      service: base.service ?? '',
      impact: 'high',
      urgency: base.urgency,
      assignment_group: '',
      assignee: 'ayse',
    });
    expect(patch).toEqual({ category: null, impact: 'high' });
  });
});
