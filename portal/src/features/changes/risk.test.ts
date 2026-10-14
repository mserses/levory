import { describe, expect, it } from 'vitest';
import { latestRiskDecision, riskInsight } from './risk';
import { makeChange, makeRiskDecision } from './testUtils';
import { makeDecision } from '@/test/fixtures';

describe('risk kararı çözümleme', () => {
  const change = makeChange();

  it('deterministik bileşenler Türkçe etiketle, anlatı ve kontrol listesi', () => {
    const insight = riskInsight(makeRiskDecision(change));
    expect(insight.deterministicScore).toBe(60);
    expect(insight.factors.map((f) => [f.label, f.points])).toContainEqual([
      'En yüksek CI kritikliği',
      25,
    ]);
    expect(insight.factors.every((f) => !/_/.test(f.label))).toBe(true);
    expect(insight.checklist).toEqual(['Yedeği doğrula', 'Bakım duyurusunu yayımla']);
    expect(insight.proposedScore).toBe(62);
    expect(insight.proposedLevel).toBe('high');
    expect(insight.currentScore).toBe(55);
    expect(insight.lowersRisk).toBe(false);
    expect(insight.applied).toBe(true);
    expect(insight.adjustment).toBe(2);
  });

  it('bozuk/eksik çıktıda çökmez; bilinmeyen bileşen ham anahtarla gösterilmez', () => {
    const insight = riskInsight(
      makeDecision({ output: { deterministic: { factors: { yeni_bilesen: 3 } } } }),
    );
    expect(insight.factors).toEqual([{ key: 'yeni_bilesen', label: 'Diğer bileşen', points: 3 }]);
    expect(insight.proposedScore).toBeNull();
    expect(insight.checklist).toEqual([]);
  });

  it('riski düşüren öneri işaretlenir', () => {
    const d = makeRiskDecision(
      change,
      { outcome: 'needs_approval' },
      { proposed: { risk_score: 40, risk_level: 'medium' }, current_risk_score: 55 },
    );
    expect(riskInsight(d).lowersRisk).toBe(true);
  });

  it('en yeni change_risk kararı seçilir', () => {
    const older = makeRiskDecision(change, { created_at: '2026-10-12T09:00:00Z' });
    const newer = makeRiskDecision(change, { created_at: '2026-10-13T09:00:00Z' });
    const other = makeDecision({ agent: 'service_desk', created_at: '2026-10-14T09:00:00Z' });
    expect(latestRiskDecision([older, other, newer])?.id).toBe(newer.id);
    expect(latestRiskDecision([])).toBeNull();
  });
});
