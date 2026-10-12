import { Badge } from '@/components';
import { stateLabel, stateTone } from '../labels';

export function ArticleStateBadge({ state }: { state: string }) {
  return (
    <Badge tone={stateTone(state)} dot>
      {stateLabel(state)}
    </Badge>
  );
}

export function KnownErrorBadge() {
  return (
    <Badge tone="danger" title="Bilinen hata kaydı (KEDB): geçici çözüm içerir">
      Bilinen hata
    </Badge>
  );
}
