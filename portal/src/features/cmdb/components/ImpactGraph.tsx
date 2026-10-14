import { useId, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import type { ConfigurationItem, ImpactAnalysis } from '@/api/types';
import { CI_CLASS_LABELS, criticalityLabel } from '@/lib/labels';
import styles from '../Cmdb.module.css';
import { NODE_HEIGHT, NODE_WIDTH, layoutImpact, type GraphNode } from '../graph';

const MAX_NAME = 22;
const shortName = (name: string) =>
  name.length > MAX_NAME ? `${name.slice(0, MAX_NAME - 1)}…` : name;

const depthText = (depth: number) =>
  depth === 0 ? 'değişen CI' : depth === 1 ? 'doğrudan etkilenir' : `${depth}. derece etkilenir`;

function nodeLabel(n: GraphNode): string {
  const cls = n.ciClass ? CI_CLASS_LABELS[n.ciClass] : 'Sınıf bilinmiyor';
  return `${n.name} — ${cls}, ${criticalityLabel(n.criticality)}, ${depthText(n.depth)}`;
}

/**
 * Erişilebilir etki grafiği (SVG): sütunlar derinlik, renk kritiklik (her düğümde "K1" metni
 * de var), etkilenen düğümler Tab ile odaklanır ve Enter/Boşluk ile CI'a gider. Aynı bilgi
 * sayfada tablo olarak da verilir.
 */
export function ImpactGraph({
  impact,
  roots,
  title,
}: {
  impact: ImpactAnalysis;
  roots: ConfigurationItem[];
  title: string;
}) {
  const navigate = useNavigate();
  const titleId = useId();
  const descId = useId();
  const markerId = useId().replace(/:/g, '');
  const layout = layoutImpact(impact, roots);
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  const go = (id: string) => {
    navigate(`/cmdb/${encodeURIComponent(id)}`);
  };
  const onKey = (event: KeyboardEvent, id: string) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      go(id);
    }
  };

  return (
    <div className={styles.graphWrap}>
      <svg
        role="group"
        aria-labelledby={titleId}
        aria-describedby={descId}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width={layout.width}
        height={layout.height}
        className={styles.graph}
      >
        <title id={titleId}>{title}</title>
        <desc id={descId}>
          Sütunlar değişen CI’dan uzaklığı gösterir. Düğüme Tab ile gidip Enter ile açabilirsiniz.
        </desc>
        <defs>
          <marker
            id={markerId}
            viewBox="0 0 10 10"
            refX="10"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className={styles.arrow} />
          </marker>
        </defs>
        {layout.edges.map((e) => {
          const from = byId.get(e.from);
          const to = byId.get(e.to);
          if (!from || !to) return null;
          return (
            <line
              key={`${e.from}-${e.to}`}
              x1={from.x + NODE_WIDTH}
              y1={from.y + NODE_HEIGHT / 2}
              x2={to.x}
              y2={to.y + NODE_HEIGHT / 2}
              className={styles.edge}
              markerEnd={`url(#${markerId})`}
              aria-hidden="true"
            />
          );
        })}
        {layout.nodes.map((n) => {
          const body = (
            <>
              <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx={8} className={styles.nodeRect} />
              <text x={10} y={21} className={styles.nodeName} aria-hidden="true">
                {shortName(n.name)}
              </text>
              <text x={10} y={40} className={styles.nodeMeta} aria-hidden="true">
                {n.root ? 'Değişen CI' : n.ciClass ? CI_CLASS_LABELS[n.ciClass] : ''}
              </text>
              {n.criticality !== null && (
                <>
                  <rect
                    x={NODE_WIDTH - 38}
                    y={8}
                    width={30}
                    height={18}
                    rx={9}
                    className={styles.critPill}
                  />
                  <text
                    x={NODE_WIDTH - 23}
                    y={21}
                    textAnchor="middle"
                    className={styles.critText}
                    aria-hidden="true"
                  >
                    {`K${n.criticality}`}
                  </text>
                </>
              )}
            </>
          );
          const common = {
            transform: `translate(${n.x} ${n.y})`,
            className: styles.node,
            'data-criticality': n.criticality ?? undefined,
            'data-depth': n.depth,
            'aria-label': nodeLabel(n),
          };
          return n.root ? (
            <g key={n.id} role="img" {...common} data-root="true">
              {body}
            </g>
          ) : (
            <g
              key={n.id}
              role="link"
              tabIndex={0}
              {...common}
              onClick={() => {
                go(n.id);
              }}
              onKeyDown={(e) => {
                onKey(e, n.id);
              }}
            >
              {body}
            </g>
          );
        })}
      </svg>
      <ul className={styles.legend} aria-label="Kritiklik renkleri">
        {[1, 2, 3, 4].map((c) => (
          <li key={c} data-criticality={c}>
            <span className={styles.legendSwatch} aria-hidden="true" />
            {criticalityLabel(c)}
          </li>
        ))}
      </ul>
    </div>
  );
}
