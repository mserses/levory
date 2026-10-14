/** Etki analizini (ImpactRead) sütunlu grafik yerleşimine çevirir: sütun = derinlik. */
import type { CIClass, ConfigurationItem, ImpactAnalysis } from '@/api/types';

export const NODE_WIDTH = 180;
export const NODE_HEIGHT = 52;
const COLUMN_GAP = 60;
const ROW_GAP = 16;
const PADDING = 16;

export interface GraphNode {
  id: string;
  name: string;
  ciClass: CIClass | null;
  criticality: number | null;
  depth: number;
  root: boolean;
  x: number;
  y: number;
}

export interface GraphEdge {
  from: string;
  to: string;
}

export interface GraphLayout {
  nodes: GraphNode[];
  edges: GraphEdge[];
  width: number;
  height: number;
}

export function layoutImpact(
  impact: ImpactAnalysis,
  roots: readonly ConfigurationItem[],
): GraphLayout {
  const columns = new Map<number, GraphNode[]>();
  const place = (node: Omit<GraphNode, 'x' | 'y'>) => {
    const column = columns.get(node.depth) ?? [];
    const full: GraphNode = {
      ...node,
      x: PADDING + node.depth * (NODE_WIDTH + COLUMN_GAP),
      y: PADDING + column.length * (NODE_HEIGHT + ROW_GAP),
    };
    column.push(full);
    columns.set(node.depth, column);
    return full;
  };
  const nodes: GraphNode[] = [];
  for (const id of impact.root_ci_ids) {
    const ci = roots.find((r) => r.id === id);
    nodes.push(
      place({
        id,
        name: ci?.name ?? 'Değişen CI',
        ciClass: ci?.ci_class ?? null,
        criticality: ci?.criticality ?? null,
        depth: 0,
        root: true,
      }),
    );
  }
  const sorted = [...impact.affected].sort((a, b) => a.depth - b.depth);
  for (const a of sorted) {
    nodes.push(
      place({
        id: a.ci.id,
        name: a.ci.name,
        ciClass: a.ci.ci_class,
        criticality: a.ci.criticality,
        depth: a.depth,
        root: false,
      }),
    );
  }
  const edges: GraphEdge[] = [];
  for (const a of impact.affected) {
    const from = a.path.length >= 2 ? a.path[a.path.length - 2] : undefined;
    if (from) edges.push({ from, to: a.ci.id });
  }
  const maxDepth = Math.max(0, ...nodes.map((n) => n.depth));
  const maxRows = Math.max(1, ...[...columns.values()].map((c) => c.length));
  return {
    nodes,
    edges,
    width: PADDING * 2 + (maxDepth + 1) * NODE_WIDTH + maxDepth * COLUMN_GAP,
    height: PADDING * 2 + maxRows * NODE_HEIGHT + (maxRows - 1) * ROW_GAP,
  };
}
