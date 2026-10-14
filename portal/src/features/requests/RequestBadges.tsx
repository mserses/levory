import type { ServiceRequest, ServiceRequestState } from '@/api/types';
import { Badge, type Tone } from '@/components/Badge';
import { SERVICE_REQUEST_STATE_LABELS } from '@/lib/labels';

const STATE_TONES: Record<ServiceRequestState, Tone> = {
  submitted: 'info',
  awaiting_approval: 'warning',
  fulfilling: 'info',
  fulfilled: 'success',
  rejected: 'danger',
  failed: 'danger',
  cancelled: 'neutral',
};

export function RequestStateBadge({ state }: { state: ServiceRequestState }) {
  return (
    <Badge tone={STATE_TONES[state]} dot>
      {SERVICE_REQUEST_STATE_LABELS[state]}
    </Badge>
  );
}

/** "İnsansız çözüldü": karşılandı + sıfır insan onayı + sıfır insan düzenlemesi (backend `touchless`) */
export function TouchlessBadge({ request }: { request: ServiceRequest }) {
  if (request.state !== 'fulfilled' || request.touchless === null) return null;
  return request.touchless ? (
    <Badge
      tone="success"
      strong
      data-touchless="true"
      title="İnsan onayı ya da müdahalesi olmadan, baştan sona otomasyonla karşılandı"
    >
      ✓ İnsansız çözüldü
    </Badge>
  ) : (
    <Badge tone="neutral" data-touchless="false">
      İnsan katkısıyla çözüldü
    </Badge>
  );
}
