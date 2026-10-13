import { useId, useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { Change } from '@/api/types';
import { Badge, Button, ErrorState, Spinner } from '@/components';
import { config } from '@/config/env';
import { formatTime } from '@/lib/format';
import { CHANGE_TYPE_LABELS } from '@/lib/labels';
import {
  changesOnDay,
  conflictMap,
  dayKey,
  freezeOnDay,
  shiftAnchor,
  visibleDays,
  type CalendarMode,
} from '../calendar';
import styles from '../Changes.module.css';
import { useCalendar } from '../hooks';
import { SegmentedControl } from './SegmentedControl';

const WEEKDAYS = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar'];

function periodLabel(mode: CalendarMode, anchor: Date, days: Date[]): string {
  if (mode === 'month') {
    return new Intl.DateTimeFormat('tr-TR', { month: 'long', year: 'numeric' }).format(anchor);
  }
  const fmt = new Intl.DateTimeFormat('tr-TR', { day: 'numeric', month: 'short', year: 'numeric' });
  const first = days[0] ?? anchor;
  const last = days[days.length - 1] ?? anchor;
  return `${fmt.format(first)} – ${fmt.format(last)}`;
}

export interface CalendarViewProps {
  mode: CalendarMode;
  anchor: Date;
  onModeChange: (mode: CalendarMode) => void;
  onAnchorChange: (anchor: Date) => void;
}

/** Hafta/ay takvimi: dondurma pencereleri gölgeli ve metinle, olası çakışmalar bayraklı */
export function CalendarView({ mode, anchor, onModeChange, onAnchorChange }: CalendarViewProps) {
  const headingId = useId();
  const days = useMemo(() => visibleDays(mode, anchor), [mode, anchor]);
  const from = days[0] ?? anchor;
  const last = days[days.length - 1] ?? anchor;
  const to = useMemo(
    () => new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1),
    [last],
  );
  const calendar = useCalendar(from, to);
  const changes = useMemo(() => calendar.data?.changes ?? [], [calendar.data]);
  const freezes = useMemo(() => calendar.data?.freeze_windows ?? [], [calendar.data]);
  const conflicts = useMemo(() => conflictMap(changes, freezes), [changes, freezes]);
  const label = periodLabel(mode, anchor, days);
  const weeks: Date[][] = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));

  return (
    <section className="stack" aria-labelledby={headingId}>
      <div className={styles.calendarBar}>
        <h2 id={headingId} className={styles.sectionTitle}>
          {label}
        </h2>
        <div className="row">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              onAnchorChange(shiftAnchor(mode, anchor, -1));
            }}
          >
            Önceki
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onAnchorChange(new Date());
            }}
          >
            Bugün
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              onAnchorChange(shiftAnchor(mode, anchor, 1));
            }}
          >
            Sonraki
          </Button>
          <SegmentedControl
            legend="Takvim aralığı"
            value={mode}
            options={[
              { value: 'week', label: 'Hafta' },
              { value: 'month', label: 'Ay' },
            ]}
            onChange={onModeChange}
          />
        </div>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        Gölgeli günler <strong>dondurma penceresi</strong> içerir. “Çakışma” işareti dondurma
        penceresi ya da aynı CI üzerinde zamanlanmış başka bir değişiklik olduğunu gösterir; kesin
        denetim zamanlama ve uygulama anında sunucuda yapılır. Saatler {config.timeZone}.
      </p>
      {calendar.isError ? (
        <ErrorState error={calendar.error} onRetry={() => void calendar.refetch()} />
      ) : calendar.isPending ? (
        <Spinner block label="Takvim yükleniyor…" />
      ) : (
        <div className={styles.calendarWrap}>
          <table className={styles.calendar} aria-busy={calendar.isFetching}>
            <caption className="visually-hidden">Değişiklik takvimi — {label}</caption>
            <thead>
              <tr>
                {WEEKDAYS.map((d) => (
                  <th key={d} scope="col">
                    <abbr title={d}>{d.slice(0, 3)}</abbr>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {weeks.map((week) => (
                <tr key={dayKey(week[0] ?? anchor)}>
                  {week.map((day) => (
                    <DayCell
                      key={dayKey(day)}
                      day={day}
                      outside={mode === 'month' && day.getMonth() !== anchor.getMonth()}
                      changes={changesOnDay(day, changes)}
                      freezeReasons={freezeOnDay(day, freezes).map(
                        (f) => f.reason || 'gerekçe yok',
                      )}
                      conflicts={conflicts}
                    />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function DayCell({
  day,
  outside,
  changes,
  freezeReasons,
  conflicts,
}: {
  day: Date;
  outside: boolean;
  changes: Change[];
  freezeReasons: string[];
  conflicts: Map<string, string[]>;
}) {
  const frozen = freezeReasons.length > 0;
  const dateText = new Intl.DateTimeFormat('tr-TR', { day: 'numeric', month: 'long' }).format(day);
  return (
    <td
      className={styles.day}
      data-testid={`day-${dayKey(day)}`}
      data-freeze={frozen}
      data-outside={outside}
    >
      <span className={styles.dayNumber}>
        <span aria-hidden="true">{day.getDate()}</span>
        <span className="visually-hidden">{dateText}</span>
      </span>
      {freezeReasons.map((reason, i) => (
        <span key={i} className={styles.freezeLabel}>
          Dondurma: {reason}
        </span>
      ))}
      {changes.length > 0 && (
        <ul className={styles.dayList}>
          {changes.map((c) => {
            const reasons = conflicts.get(c.id);
            const name = `${c.number} ${c.title} (${CHANGE_TYPE_LABELS[c.change_type]}, ${formatTime(c.planned_start)})${
              reasons ? ` — Çakışma: ${reasons.join('; ')}` : ''
            }`;
            return (
              <li key={c.id}>
                <Link
                  to={`/degisiklikler/${encodeURIComponent(c.id)}`}
                  className={styles.dayItem}
                  aria-label={name}
                  title={reasons?.join('\n')}
                >
                  <span className={styles.dayTime}>{formatTime(c.planned_start)}</span>
                  <span className={styles.dayTitle}>
                    {c.number} · {c.title}
                  </span>
                  {reasons && (
                    <Badge tone="danger" dot strong>
                      Çakışma
                    </Badge>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </td>
  );
}
