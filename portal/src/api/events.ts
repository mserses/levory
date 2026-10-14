import { api } from './client';
import type {
  AgentResult,
  EventGroup,
  EventGroupDetail,
  EventGroupListQuery,
  MonitoringEvent,
} from './types';

const groupPath = (id: string) => `/events/groups/${encodeURIComponent(id)}`;

export interface SilenceBody {
  /** Susturma süresi (dk): 5 – 10080 (7 gün); backend `SilenceArgs` */
  minutes: number;
  /** Gerekçe (zorunlu, en fazla 500 karakter) */
  comment: string;
}

/**
 * İzleme / olay yönetimi uçları (`api/events.py`, docs/faz4.md). Okuma: agent / admin.
 *
 * `acknowledge` (`event.acknowledge`, R1) ve `silence` (`event.silence`, R2) agent / admin; sorun ucu
 * `POST /problems/{id}/fix` ile aynı biçimde araç boru hattından kullanıcının kimliğiyle geçer,
 * `AgentResultRead` döner. `event.silence` her zaman R2 olduğundan yanıt `status: "pending_approval"`
 * + `action_request_id` taşır (başka bir onaylayan /onaylar → Eylem onayları'ndan onaylar).
 */
export const eventsApi = {
  listGroups: (query: EventGroupListQuery = {}, signal?: AbortSignal) =>
    api.get<{ items: EventGroup[] }>('/events/groups', { query, signal }),

  getGroup: (id: string, signal?: AbortSignal) =>
    api.get<EventGroupDetail>(groupPath(id), { signal }),

  listEvents: (query: { group_id?: string; limit?: number } = {}, signal?: AbortSignal) =>
    api.get<{ items: MonitoringEvent[] }>('/events', { query, signal }),

  /** `POST /events/groups/{id}/acknowledge` → `event.acknowledge` (R1) */
  acknowledge: (id: string, note = '') =>
    api.post<AgentResult>(`${groupPath(id)}/acknowledge`, { note }),

  /** `POST /events/groups/{id}/silence` → `event.silence` (R2, onay ister) */
  silence: (id: string, body: SilenceBody) =>
    api.post<AgentResult>(`${groupPath(id)}/silence`, body),
};
