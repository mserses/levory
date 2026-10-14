import { Badge } from '@/components';
import { cx } from '@/lib/cx';
import styles from '../Approvals.module.css';
import { displayValue, fieldLabel, type DiffRow } from '../proposal';

interface ProposalDiffProps {
  rows: DiffRow[];
  caption: string;
  /** Kayıt yüklenirken/yüklenemediğinde "mevcut" sütunu için not */
  currentNote?: string | undefined;
}

const STATE_TEXT = { change: 'Değişecek', same: 'Aynı kalır', unknown: 'Bilinmiyor' } as const;
const STATE_TONE = { change: 'accent', same: 'neutral', unknown: 'neutral' } as const;

/** Mevcut ↔ önerilen alan tablosu. Dar ekranda satırlar kart gibi dizilir. */
export function ProposalDiff({ rows, caption, currentNote }: ProposalDiffProps) {
  if (rows.length === 0) {
    return <p className="muted">Bu karar alan değişikliği önermiyor.</p>;
  }
  return (
    <div className={styles.diffWrap}>
      <table className={styles.diff}>
        <caption className={styles.diffCaption}>
          {caption}
          {currentNote && <span className="muted"> · {currentNote}</span>}
        </caption>
        <thead>
          <tr>
            <th scope="col">Alan</th>
            <th scope="col">Mevcut</th>
            <th scope="col">Önerilen</th>
            <th scope="col">Sonuç</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.field}
              className={cx(row.state === 'change' && styles.rowChanged)}
              data-state={row.state}
            >
              <th scope="row">{fieldLabel(row.field)}</th>
              <td data-label="Mevcut">{displayValue(row.field, row.current)}</td>
              <td data-label="Önerilen">
                <strong>{displayValue(row.field, row.proposed)}</strong>
              </td>
              <td data-label="Sonuç">
                <Badge tone={STATE_TONE[row.state]}>{STATE_TEXT[row.state]}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
