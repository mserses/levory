import { useId, type ReactNode } from 'react';
import styles from './Charts.module.css';

export const COLOR_CLASSES = [styles.c0, styles.c1, styles.c2, styles.c3, styles.c4] as const;
/** İkincil kodlama: renk körlüğü / baskı için her seri ayrıca çizgi deseniyle ayrılır */
export const DASHES = ['', '6 3', '2 3', '8 3 2 3', '1 3'] as const;

export const colorClass = (i: number) => COLOR_CLASSES[i % COLOR_CLASSES.length] ?? styles.c0;
export const dash = (i: number) => DASHES[i % DASHES.length] ?? '';

export interface TableData {
  head: readonly string[];
  rows: readonly (readonly string[])[];
}

interface ChartFigureProps {
  title: string;
  description?: string | undefined;
  legend?: ReactNode;
  table: TableData;
  children: ReactNode;
}

/** Ortak grafik çerçevesi: figür başlığı, açıklama, seri açıklaması, grafik ve veri tablosu */
export function ChartFigure({ title, description, legend, table, children }: ChartFigureProps) {
  const id = useId();
  return (
    <figure className={styles.figure} aria-labelledby={`${id}-t`}>
      <figcaption className={styles.caption}>
        <h3 id={`${id}-t`} className={styles.title}>
          {title}
        </h3>
        {description && <p className={styles.description}>{description}</p>}
      </figcaption>
      {legend}
      {children}
      <details className={styles.details}>
        <summary>Veri tablosu</summary>
        <table className={styles.dataTable}>
          <caption className="visually-hidden">{`${title} — veri tablosu`}</caption>
          <thead>
            <tr>
              {table.head.map((h) => (
                <th key={h} scope="col">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={`${String(i)}-${row[0] ?? ''}`}>
                {row.map((cell, j) =>
                  j === 0 ? (
                    <th key={j} scope="row">
                      {cell}
                    </th>
                  ) : (
                    <td key={j}>{cell}</td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

export function Swatch({ index }: { index: number }) {
  return (
    <svg className={styles.swatch} viewBox="0 0 18 10" aria-hidden="true" focusable="false">
      <line
        x1="1"
        y1="5"
        x2="17"
        y2="5"
        className={`${styles.line} ${colorClass(index)}`}
        strokeDasharray={dash(index)}
      />
    </svg>
  );
}
