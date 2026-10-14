import { Badge } from '@/components';

/** Sunucunun hesapladığı `target_met` (metin + renk; ölçülmemişse hiçbir şey) */
export function TargetBadge({ met }: { met: boolean | null }) {
  if (met === null) return null;
  return met ? (
    <Badge tone="success">Hedef tuttu</Badge>
  ) : (
    <Badge tone="warning">Hedefin gerisinde</Badge>
  );
}
