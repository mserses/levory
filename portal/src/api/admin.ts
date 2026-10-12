import { api } from './client';
import type {
  AuditPage,
  AuditQuery,
  AuditVerify,
  ExternalSystem,
  SyncEvent,
  SystemStatus,
} from './types';

export const auditApi = {
  list: (query: AuditQuery = {}, signal?: AbortSignal) =>
    api.get<AuditPage>('/audit', { query, signal }),
  verify: (signal?: AbortSignal) => api.get<AuditVerify>('/audit/verify', { signal }),
};

export const integrationsApi = {
  status: (signal?: AbortSignal) => api.get<SystemStatus[]>('/integrations/status', { signal }),
  pull: (system: ExternalSystem) =>
    api.post<Record<string, unknown>>(`/integrations/${encodeURIComponent(system)}/pull`),
  push: (incidentId: string, system?: ExternalSystem) =>
    api.post<SyncEvent[]>(`/integrations/push/${encodeURIComponent(incidentId)}`, undefined, {
      query: { system },
    }),
  conflicts: (signal?: AbortSignal) => api.get<SyncEvent[]>('/integrations/conflicts', { signal }),
  resolveConflict: (eventId: string, winner: 'local' | 'remote') =>
    api.post<SyncEvent>(
      `/integrations/conflicts/${encodeURIComponent(eventId)}/resolve`,
      undefined,
      {
        query: { winner },
      },
    ),
};
