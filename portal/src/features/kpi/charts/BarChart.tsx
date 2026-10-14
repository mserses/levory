import { formatKpiValue, type KpiUnit } from '../model';
import { ChartFigure, colorClass } from './ChartFigure';
import styles from './Charts.module.css';

export interface BarChartProps {
  title: string;
  description?: string;
  unit: KpiUnit;
  items: readonly { label: string; value: number }[];
}

const W = 640;
const ROW = 30;
const LABEL_W = 132;
const VALUE_W = 56;

/** Yatay çubuk grafik (tek seri, tek renk; değer etiketleri çubuğun yanında) */
export function BarChart({ title, description, unit, items }: BarChartProps) {
  const max = Math.max(1, ...items.map((i) => i.value));
  const H = items.length * ROW + 4;
  const innerW = W - LABEL_W - VALUE_W;
  const label = `${title}: ${items.map((i) => `${i.label} ${formatKpiValue(i.value, unit)}`).join(', ')}`;
  return (
    <ChartFigure
      title={title}
      description={description}
      table={{
        head: ['Kova', 'Değer'],
        rows: items.map((i) => [i.label, formatKpiValue(i.value, unit)]),
      }}
    >
      <svg
        className={styles.svg}
        viewBox={`0 0 ${String(W)} ${String(H)}`}
        role="img"
        aria-label={label}
      >
        {items.map((item, i) => {
          const w = item.value > 0 ? Math.max(4, (innerW * item.value) / max) : 0;
          const yTop = i * ROW + 4;
          return (
            <g key={item.label}>
              <title>{`${item.label}: ${formatKpiValue(item.value, unit)}`}</title>
              <text className={styles.tick} x={LABEL_W - 8} y={yTop + 15} textAnchor="end">
                {item.label}
              </text>
              <rect
                className={`${styles.bar} ${colorClass(0)}`}
                x={LABEL_W}
                y={yTop + 2}
                width={w}
                height={ROW - 10}
                rx={4}
              />
              <text className={styles.barValue} x={LABEL_W + w + 6} y={yTop + 16}>
                {formatKpiValue(item.value, unit)}
              </text>
            </g>
          );
        })}
      </svg>
    </ChartFigure>
  );
}
