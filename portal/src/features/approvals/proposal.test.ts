import { describe, expect, it } from 'vitest';
import { makeIncident } from '@/test/fixtures';
import {
  buildConflictRows,
  buildDiff,
  classifyPriorityChange,
  displayValue,
  isLikelyStale,
  parseConflictPayload,
  parseProposal,
  priorityChange,
} from './proposal';

describe('classifyPriorityChange', () => {
  it.each([
    ['P4', 'P1', 'urgent_raise'],
    ['P3', 'P2', 'urgent_raise'],
    ['P2', 'P1', 'urgent_raise'],
    ['P1', 'P3', 'urgent_downgrade'],
    ['P2', 'P4', 'urgent_downgrade'],
    ['P5', 'P3', 'raise'],
    ['P3', 'P5', 'lower'],
    ['P3', 'P3', 'same'],
  ] as const)('%s → %s = %s', (current, proposed, kind) => {
    expect(classifyPriorityChange(current, proposed)).toBe(kind);
  });
});

describe('parseProposal', () => {
  it('serbest JSON çıktıyı güvenle okur, bilinmeyen tipleri atlar', () => {
    const parsed = parseProposal({
      proposed: { impact: 'high', urgency: 'high', bogus: 42 },
      current_priority: 'P4',
      proposed_priority: 'P1',
      applied_fields: ['category', 7],
      policy_reason: 'İnsan onayı gerekli.',
    });
    expect(parsed.proposed).toEqual({ impact: 'high', urgency: 'high' });
    expect(parsed.decisionPriority).toBe('P4');
    expect(parsed.proposedPriority).toBe('P1');
    expect(parsed.appliedFields).toEqual(['category']);
    expect(parsed.policyReason).toBe('İnsan onayı gerekli.');
  });

  it('proposed.priority biçimini de anlar; bozuk çıktıda boş döner', () => {
    expect(parseProposal({ proposed: { priority: 'P3' } }).proposedPriority).toBe('P3');
    const empty = parseProposal({ proposed: 'x', proposed_priority: 'P9' });
    expect(empty.proposed).toEqual({});
    expect(empty.proposedPriority).toBeNull();
  });
});

describe('buildDiff', () => {
  const parsed = parseProposal({
    proposed: { urgency: 'high', category: 'ag', impact: 'high' },
    current_priority: 'P4',
    proposed_priority: 'P1',
  });

  it('kaydın güncel değerleriyle karşılaştırır; öncelik en sonda', () => {
    const incident = makeIncident({
      impact: 'low',
      urgency: 'high',
      category: 'ag',
      priority: 'P4',
    });
    const rows = buildDiff({ incident_priority: 'P4' }, parsed, incident);
    expect(rows.map((r) => [r.field, r.state])).toEqual([
      ['category', 'same'],
      ['impact', 'change'],
      ['urgency', 'same'],
      ['priority', 'change'],
    ]);
  });

  it('kayıt yoksa alanlar bilinmiyor, öncelik inbox verisinden', () => {
    const rows = buildDiff({ incident_priority: 'P4' }, parsed, undefined);
    expect(rows.find((r) => r.field === 'impact')?.state).toBe('unknown');
    expect(rows.find((r) => r.field === 'priority')).toMatchObject({
      current: 'P4',
      state: 'change',
    });
  });

  it('priorityChange canlı önceliği kullanır', () => {
    expect(priorityChange({ incident_priority: 'P2' }, parsed)).toEqual({
      current: 'P2',
      proposed: 'P1',
      kind: 'urgent_raise',
    });
    expect(priorityChange({ incident_priority: null }, parseProposal({}))).toBeNull();
  });
});

describe('isLikelyStale', () => {
  it('sürümler farklıysa bayat', () => {
    expect(isLikelyStale({ incident_version: 2, incident_current_version: 3 })).toBe(true);
    expect(isLikelyStale({ incident_version: 3, incident_current_version: 3 })).toBe(false);
    expect(isLikelyStale({ incident_version: null, incident_current_version: 3 })).toBe(false);
  });
});

describe('displayValue', () => {
  it('enumları Türkçe etiketler, boşu tire yapar', () => {
    expect(displayValue('priority', 'P1')).toBe('P1 · Kritik');
    expect(displayValue('status', 'in_progress')).toBe('Çalışılıyor');
    expect(displayValue('category', 'ag')).toBe('Ağ ve bağlantı');
    expect(displayValue('impact', null)).toBe('—');
  });
});

describe('conflict payload', () => {
  it('yerel ve uzak alanları hizalar, kimlik alanlarını dışarıda bırakır', () => {
    const parsed = parseConflictPayload({
      incident_id: 'i-1',
      local_version: 4,
      local: { title: 'VPN', status: 'in_progress', priority: 'P3', resolution: null },
      remote: {
        external_id: 'abc',
        number: 'INC0010001',
        title: 'VPN ',
        status: 'resolved',
        priority: 'P3',
        updated_at: '2026-10-12 10:00:00',
      },
    });
    expect(parsed.incidentId).toBe('i-1');
    expect(parsed.localVersion).toBe(4);
    const rows = buildConflictRows(parsed);
    expect(rows.map((r) => r.field)).toEqual(['title', 'status', 'priority', 'resolution']);
    expect(rows.find((r) => r.field === 'title')?.differs).toBe(false); // boşluk farkı yok sayılır
    expect(rows.find((r) => r.field === 'status')?.differs).toBe(true);
    expect(rows.find((r) => r.field === 'resolution')).toMatchObject({
      remoteMissing: true,
      differs: false,
    });
  });

  it('bozuk yükte boş döner', () => {
    const parsed = parseConflictPayload({ local: 'x' });
    expect(buildConflictRows(parsed)).toEqual([]);
    expect(parsed.incidentId).toBeNull();
  });
});
