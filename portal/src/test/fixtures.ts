/** Test verisi fabrikaları: API tipleriyle birebir uyumlu nesneler üretir. */
import type { AIDecision, Article, DecisionInboxItem, Incident } from '@/api/types';

let seq = 0;
const nextId = () => `00000000-0000-4000-8000-${String(++seq).padStart(12, '0')}`;

export const FIXED_NOW = new Date('2026-10-12T09:00:00Z');

export function makeIncident(overrides: Partial<Incident> = {}): Incident {
  const n = ++seq;
  return {
    id: nextId(),
    number: `INC${String(n).padStart(7, '0')}`,
    kind: 'incident',
    title: 'VPN bağlantısı kurulamıyor',
    description: 'Evden VPN ile bağlanamıyorum.',
    caller: 'zeynep',
    category: 'ag',
    service: 'vpn',
    impact: 'low',
    urgency: 'medium',
    priority: 'P4',
    status: 'new',
    assignment_group: null,
    assignee: null,
    resolution: null,
    response_due_at: '2026-10-12T17:00:00Z',
    resolve_due_at: '2026-10-15T09:00:00Z',
    responded_at: null,
    resolved_at: null,
    on_hold_reason: null,
    sla_paused_at: null,
    sla_paused_seconds: 0,
    ai_confidence: null,
    version: 1,
    created_at: '2026-10-12T09:00:00Z',
    updated_at: '2026-10-12T09:00:00Z',
    ...overrides,
  };
}

export function makeDecision(overrides: Partial<AIDecision> = {}): AIDecision {
  return {
    id: nextId(),
    incident_id: null,
    agent: 'service_desk',
    action: 'triage',
    input_summary: 'VPN bağlantısı kurulamıyor',
    output: { proposed: { priority: 'P3' } },
    rationale: 'Benzer kayıtlar ağ ekibince çözülmüş.',
    sources: [],
    model: 'claude-opus-5-5',
    confidence: 0.82,
    outcome: 'suggested',
    incident_version: 1,
    approved_by: null,
    approved_at: null,
    created_at: '2026-10-12T09:05:00Z',
    ...overrides,
  };
}

export function makeInboxItem(overrides: Partial<DecisionInboxItem> = {}): DecisionInboxItem {
  const incident = makeIncident();
  return {
    ...makeDecision({ incident_id: incident.id }),
    incident_number: incident.number,
    incident_title: incident.title,
    incident_priority: incident.priority,
    incident_status: incident.status,
    incident_current_version: 1,
    ...overrides,
  };
}

export function makeArticle(overrides: Partial<Article> = {}): Article {
  const n = ++seq;
  return {
    id: nextId(),
    number: `KB${String(n).padStart(7, '0')}`,
    title: 'VPN sertifikası nasıl yenilenir?',
    body: 'Adım 1: ...',
    category: 'ag',
    is_known_error: false,
    workaround: null,
    state: 'published',
    source_incident_ids: [],
    valid_until: null,
    created_at: '2026-10-01T09:00:00Z',
    updated_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}
