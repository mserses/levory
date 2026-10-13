import type { ScheduleConflict } from '@/api/types';
import { formatDateTime } from '@/lib/format';

/** Sunucunun zamanlama çakışmaları (GET /conflicts ya da 409 gövdesi) */
export function ConflictList({ conflicts }: { conflicts: readonly ScheduleConflict[] }) {
  return (
    <ul style={{ margin: 0 }}>
      {conflicts.map((c) => (
        <li key={`${c.kind}-${c.ref_id}`}>
          <strong>
            {c.kind === 'freeze_window' ? 'Dondurma penceresi' : (c.ref_number ?? 'Değişiklik')}
          </strong>
          {' — '}
          {c.description} ({formatDateTime(c.start)} – {formatDateTime(c.end)})
        </li>
      ))}
    </ul>
  );
}
