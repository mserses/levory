import type { EventGroup, EventGroupDetail, MonitoringEvent } from '@/api/types';

export function makeEventGroup(over: Partial<EventGroup> = {}): EventGroup {
  return {
    id: 'grp-1',
    number: 'EVG0000001',
    state: 'open',
    title: 'db-prod-01 erişilemiyor',
    root_ci_id: 'ci-db',
    fingerprint: 'fp-1',
    severity: 'critical',
    event_count: 3,
    first_event_at: '2026-10-12T08:40:00Z',
    last_event_at: '2026-10-12T08:55:00Z',
    last_change_at: '2026-10-12T08:55:00Z',
    resolved_at: null,
    flapping: false,
    incident_id: null,
    noted_at: null,
    merged_into_id: null,
    acknowledged_by: null,
    acknowledged_at: null,
    silenced_until: null,
    silenced_by: null,
    version: 1,
    ...over,
  };
}

export function makeEvent(over: Partial<MonitoringEvent> = {}): MonitoringEvent {
  return {
    id: 'ev-1',
    source: 'alertmanager',
    fingerprint: 'fp-1',
    status: 'firing',
    severity: 'critical',
    title: 'PostgreSQL erişilemiyor',
    labels: { instance: 'db-prod-01:9100' },
    annotations: {},
    starts_at: '2026-10-12T08:40:00Z',
    ends_at: null,
    ci_id: 'ci-db',
    ci_rule: 'instance',
    group_id: 'grp-1',
    suppressed: null,
    received_count: 1,
    last_received_at: '2026-10-12T08:40:00Z',
    changed_at: '2026-10-12T08:40:00Z',
    ...over,
  };
}

export function makeGroupDetail(over: Partial<EventGroupDetail> = {}): EventGroupDetail {
  return {
    group: makeEventGroup(),
    root_ci_name: 'db-prod-01',
    proposed_priority: 'P1',
    actionable: true,
    pending_action_request_id: null,
    members: [
      {
        event_id: 'ev-1',
        title: 'PostgreSQL erişilemiyor',
        status: 'firing',
        severity: 'critical',
        ci_id: 'ci-db',
        ci_name: 'db-prod-01',
        path: ['db-prod-01'],
        suppressed: null,
      },
      {
        event_id: 'ev-2',
        title: 'Sipariş API 5xx',
        status: 'firing',
        severity: 'high',
        ci_id: 'ci-api',
        ci_name: 'order-api',
        path: ['db-prod-01', 'order-api'],
        suppressed: null,
      },
    ],
    events: [makeEvent(), makeEvent({ id: 'ev-2', title: 'Sipariş API 5xx', severity: 'high' })],
    ...over,
  };
}
