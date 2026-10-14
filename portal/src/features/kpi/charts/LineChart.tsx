import { useState } from 'react';
import { formatKpiValue, type KpiUnit } from '../model';
import { ChartFigure, Swatch, colorClass, dash } from './ChartFigure';
import styles from './Charts.module.css';

export interface LineSeries {
  key: string;
  label: string;
  values: readonly (number | null)[];
  /** Kategorik renk sırası (varlığa bağlı, sıraya değil: süzgeç rengi değiştirmez) */
  colorIndex: number;
}

export interface LineChartProps {
  title: string;
  description?: string;
  unit: KpiUnit;
  xLabels: readonly string[];
  series: readonly LineSeries[];
}

const W = 640;
const H = 220;
const PAD = { top: 12, right: 16, bottom: 28, left: 56 };
const TICKS = 4;

function niceMax(max: number, unit: KpiUnit): number {
  if (unit === 'percent') return 100;
  if (max <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(max));
  const f = max / exp;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * exp;
}

/** "P3 · Orta: 9 sa → 6 sa" — ekran okuyucu için seri başına ilk ve son değer */
function summary(title: string, series: readonly LineSeries[], unit: KpiUnit): string {
  const parts = series.map((s) => {
    const vals = s.values.filter((v): v is number => v !== null);
    if (vals.length === 0) return `${s.label}: veri yok`;
    const first = formatKpiValue(vals[0], unit);
    const last = formatKpiValue(vals.at(-1), unit);
    return vals.length === 1 ? `${s.label}: ${first}` : `${s.label}: ${first} → ${last}`;
  });
  return `${title}. ${parts.join('; ')}`;
}

/** Küçük çok serili çizgi grafiği (tek y ekseni). Erişilebilir alternatif: veri tablosu. */
export function LineChart({ title, description, unit, xLabels, series }: LineChartProps) {
  const [active, setActive] = useState<number | null>(null);
  const n = xLabels.length;
  const hasData = series.some((s) => s.values.some((v) => v !== null));
  const max = niceMax(
    Math.max(0, ...series.flatMap((s) => s.values.filter((v): v is number => v !== null))),
    unit,
  );
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (n <= 1 ? innerW / 2 : (innerW * i) / (n - 1));
  const y = (v: number) => PAD.top + innerH - (innerH * v) / max;
  const step = n <= 1 ? innerW : innerW / (n - 1);

  const table = {
    head: ['Dönem', ...series.map((s) => s.label)],
    rows: xLabels.map((label, i) => [
      label,
      ...series.map((s) => formatKpiValue(s.values[i] ?? null, unit)),
    ]),
  };
  const legend =
    series.length >= 2 ? (
      <ul className={styles.legend} aria-label="Seriler">
        {series.map((s) => (
          <li key={s.key}>
            <Swatch index={s.colorIndex} />
            {s.label}
          </li>
        ))}
      </ul>
    ) : null;

  return (
    <ChartFigure title={title} description={description} legend={legend} table={table}>
      {!hasData ? (
        <p className={styles.empty}>Bu dönemde gösterilecek veri yok.</p>
      ) : (
        <div className={styles.plot}>
          <svg
            className={styles.svg}
            viewBox={`0 0 ${String(W)} ${String(H)}`}
            role="img"
            aria-label={summary(title, series, unit)}
          >
            {Array.from({ length: TICKS + 1 }, (_, t) => {
              const v = (max * t) / TICKS;
              return (
                <g key={t}>
                  <line
                    className={t === 0 ? styles.axis : styles.grid}
                    x1={PAD.left}
                    x2={W - PAD.right}
                    y1={y(v)}
                    y2={y(v)}
                  />
                  <text className={styles.tick} x={PAD.left - 8} y={y(v) + 4} textAnchor="end">
                    {formatKpiValue(v, unit)}
                  </text>
                </g>
              );
            })}
            {xLabels.map((label, i) => (
              <text key={label} className={styles.tick} x={x(i)} y={H - 8} textAnchor="middle">
                {label}
              </text>
            ))}
            {active !== null && (
              <line
                className={styles.crosshair}
                x1={x(active)}
                x2={x(active)}
                y1={PAD.top}
                y2={PAD.top + innerH}
              />
            )}
            {series.map((s) => (
              <g key={s.key}>
                {segments(s.values).map((seg) => (
                  <path
                    key={seg[0]}
                    className={`${styles.line} ${colorClass(s.colorIndex)}`}
                    strokeDasharray={dash(s.colorIndex)}
                    d={seg
                      .map(
                        (i, k) =>
                          `${k === 0 ? 'M' : 'L'}${String(x(i))},${String(y(s.values[i] ?? 0))}`,
                      )
                      .join(' ')}
                  />
                ))}
                {s.values.map((v, i) =>
                  v === null ? null : (
                    <circle
                      key={i}
                      className={`${styles.marker} ${colorClass(s.colorIndex)}`}
                      cx={x(i)}
                      cy={y(v)}
                      r={active === i ? 5 : 4}
                    />
                  ),
                )}
              </g>
            ))}
            {xLabels.map((label, i) => (
              <rect
                key={label}
                data-testid={`hover-${String(i)}`}
                className={styles.hit}
                x={x(i) - step / 2}
                y={PAD.top}
                width={step}
                height={innerH}
                onMouseEnter={() => {
                  setActive(i);
                }}
                onMouseLeave={() => {
                  setActive(null);
                }}
              />
            ))}
          </svg>
          {active !== null && (
            <div
              className={styles.tooltip}
              data-testid="chart-tooltip"
              aria-hidden="true"
              style={{
                left: `${String(Math.min(75, (x(active) / W) * 100))}%`,
              }}
            >
              <strong>{xLabels[active]}</strong>
              {series.map((s) => (
                <div key={s.key} className={styles.tooltipRow}>
                  <span>
                    <Swatch index={s.colorIndex} />
                    {s.label}
                  </span>
                  <span>{formatKpiValue(s.values[active] ?? null, unit)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </ChartFigure>
  );
}

/** Boş değerlerde kırılan ardışık indeks dizileri */
function segments(values: readonly (number | null)[]): number[][] {
  const out: number[][] = [];
  let cur: number[] = [];
  values.forEach((v, i) => {
    if (v === null) {
      if (cur.length) out.push(cur);
      cur = [];
    } else cur.push(i);
  });
  if (cur.length) out.push(cur);
  return out;
}
