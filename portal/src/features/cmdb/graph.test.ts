import { describe, expect, it } from 'vitest';
import { makeCI, makeImpact } from '@/features/changes/testUtils';
import { layoutImpact } from './graph';

describe('etki grafiği yerleşimi', () => {
  const root = makeCI({ id: 'r', name: 'kök' });
  const a = makeCI({ id: 'a', name: 'a' });
  const b = makeCI({ id: 'b', name: 'b' });
  const c = makeCI({ id: 'c', name: 'c' });
  const impact = makeImpact(
    [root],
    [
      { ci: a, depth: 1, path: ['r', 'a'] },
      { ci: b, depth: 1, path: ['r', 'b'] },
      { ci: c, depth: 2, path: ['r', 'a', 'c'] },
    ],
  );

  it('düğümler derinliğe göre sütunlara, aynı derinlikte alt alta dizilir', () => {
    const layout = layoutImpact(impact, [root]);
    const pos = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
    expect(pos.r!.depth).toBe(0);
    expect(pos.a!.x).toBe(pos.b!.x);
    expect(pos.a!.y).toBeLessThan(pos.b!.y);
    expect(pos.c!.x).toBeGreaterThan(pos.a!.x);
    expect(layout.width).toBeGreaterThan(pos.c!.x);
    expect(layout.height).toBeGreaterThan(pos.b!.y);
  });

  it('kenarlar yol üzerindeki önceki düğümden gelir', () => {
    const layout = layoutImpact(impact, [root]);
    expect(layout.edges.map((e) => `${e.from}->${e.to}`).sort()).toEqual(['a->c', 'r->a', 'r->b']);
  });

  it('bilinmeyen kök adı yerine "Değişen CI" yazar', () => {
    const layout = layoutImpact(impact, []);
    expect(layout.nodes.find((n) => n.id === 'r')?.name).toBe('Değişen CI');
  });
});
