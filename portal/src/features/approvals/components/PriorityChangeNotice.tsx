import type { Priority } from '@/api/types';
import { Badge, PriorityBadge } from '@/components';
import type { PriorityChangeKind } from '../proposal';
import styles from '../Approvals.module.css';

const TEXT: Record<Exclude<PriorityChangeKind, 'same'>, string> = {
  urgent_raise: 'Kritik yükseltme',
  urgent_downgrade: 'P1/P2’den düşürme',
  raise: 'Öncelik yükseltme',
  lower: 'Öncelik düşürme',
};

const EXPLAIN: Partial<Record<PriorityChangeKind, string>> = {
  urgent_raise:
    'P1/P2’ye yükseltme yapay zekâ tarafından uygulanamaz; onayınızla kayıt kritik önceliğe geçer.',
  urgent_downgrade:
    'P1/P2 öncelikli bir kaydın düşürülmesi insan onayı gerektirir; etkisini doğrulayın.',
};

interface Props {
  change: { current: Priority; proposed: Priority; kind: PriorityChangeKind };
}

/** Öncelik değişikliğini vurgular; renk tek başına anlam taşımaz (metin + ok). */
export function PriorityChangeNotice({ change }: Props) {
  if (change.kind === 'same') return null;
  const urgent = change.kind === 'urgent_raise' || change.kind === 'urgent_downgrade';
  return (
    <div
      className={urgent ? styles.priorityUrgent : styles.priorityChange}
      data-priority-change={change.kind}
    >
      <div className="row">
        <Badge
          tone={urgent ? (change.kind === 'urgent_raise' ? 'danger' : 'warning') : 'info'}
          strong
        >
          {TEXT[change.kind]}
        </Badge>
        <PriorityBadge priority={change.current} compact />
        <span aria-hidden="true">→</span>
        <span className="visually-hidden">, önerilen:</span>
        <PriorityBadge priority={change.proposed} compact />
      </div>
      {EXPLAIN[change.kind] && <p className={styles.noticeText}>{EXPLAIN[change.kind]}</p>}
    </div>
  );
}
