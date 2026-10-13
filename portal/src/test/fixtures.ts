/** Test verisi fabrikaları: API tipleriyle birebir uyumlu nesneler üretir. */
import type {
  ActionRequest,
  AIDecision,
  Article,
  CatalogItem,
  DecisionInboxItem,
  Incident,
  Problem,
  ServiceRequest,
} from '@/api/types';

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

// --- Faz 3 ---------------------------------------------------------------------------------

export function makeCatalogItem(overrides: Partial<CatalogItem> = {}): CatalogItem {
  return {
    id: nextId(),
    key: 'standard_software_group',
    name: 'Standart yazılım grubu erişimi',
    description: 'Standart yazılım paketlerinin dağıtıldığı gruba eklenirsiniz.',
    category: 'Kimlik ve erişim',
    form_schema: {
      type: 'object',
      properties: {
        group: { type: 'string', format: 'group', title: 'Yazılım grubu' },
        justification: { type: 'string', title: 'Gerekçe', maxLength: 500 },
      },
      required: ['group'],
    },
    approval_policy: 'none',
    approval_group: null,
    fulfilment_plan: [
      {
        tool: 'identity.add_to_group',
        title: 'Yazılım grubuna ekle',
        args: { user: '{{requester}}', group: '{{form.group}}' },
      },
    ],
    standard: true,
    active: true,
    version: 1,
    created_at: '2026-10-01T09:00:00Z',
    updated_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}

export function makeServiceRequest(overrides: Partial<ServiceRequest> = {}): ServiceRequest {
  const n = ++seq;
  return {
    id: nextId(),
    number: `REQ${String(n).padStart(7, '0')}`,
    catalog_item_id: nextId(),
    item_key: 'standard_software_group',
    item_name: 'Standart yazılım grubu erişimi',
    standard: true,
    requester: 'zeynep',
    opened_by: 'zeynep',
    form_values: { group: 'sg-office-apps' },
    state: 'fulfilled',
    approval_policy: 'none',
    approval_state: 'not_required',
    approver: null,
    approved_by: null,
    approved_at: null,
    approval_comment: '',
    plan: [
      { tool: 'identity.add_to_group', title: 'Yazılım grubuna ekle', args: {} },
      { tool: 'notify.requester', title: 'Talep edene bildir', args: {} },
    ],
    current_step: 2,
    attempt: 0,
    step_results: [
      {
        index: 0,
        title: 'Yazılım grubuna ekle',
        tool: 'identity.add_to_group',
        risk_class: 'R1',
        status: 'executed',
        actor: 'ai:fulfilment',
        at: '2026-10-12T09:00:05Z',
        result: { added: true },
      },
      {
        index: 1,
        title: 'Talep edene bildir',
        tool: 'notify.requester',
        risk_class: 'R1',
        status: 'executed',
        actor: 'ai:fulfilment',
        at: '2026-10-12T09:00:06Z',
      },
    ],
    waiting_action_id: null,
    error: null,
    human_approvals: 0,
    human_edits: 0,
    touchless: true,
    touchless_reason: null,
    touches: [],
    version: 4,
    created_at: '2026-10-12T09:00:00Z',
    updated_at: '2026-10-12T09:00:06Z',
    fulfilled_at: '2026-10-12T09:00:06Z',
    closed_at: '2026-10-12T09:00:06Z',
    actions: [],
    ...overrides,
  };
}

export function makeActionRequest(overrides: Partial<ActionRequest> = {}): ActionRequest {
  const n = ++seq;
  return {
    id: nextId(),
    number: `ACT${String(n).padStart(7, '0')}`,
    tool: 'kb.publish',
    tool_description: 'Taslak bilgi bankası makalesini yayımlar.',
    risk_class: 'R2',
    state: 'pending',
    required_approvals: 1,
    requested_by: 'ai:knowledge',
    reason: 'Makale üç olayda çözüm olarak kullanıldı.',
    confidence: 0.86,
    args_display: { article_id: 'a-1' },
    incident_id: null,
    change_id: null,
    request_id: null,
    decision_id: null,
    result: null,
    error: null,
    executed_by: null,
    executed_at: null,
    expires_at: '2099-01-01T09:00:00Z',
    version: 1,
    created_at: '2026-10-12T09:00:00Z',
    updated_at: '2026-10-12T09:00:00Z',
    approvals: [],
    ...overrides,
  };
}

export function makeProblem(overrides: Partial<Problem> = {}): Problem {
  const n = ++seq;
  return {
    id: nextId(),
    number: `PRB${String(n).padStart(7, '0')}`,
    title: 'VPN ağ geçidi tekrarlayan bağlantı kopması',
    description: 'Son 14 günde aynı ağ geçidinde 5 olay.',
    state: 'investigating',
    priority: 'P3',
    category: 'ag',
    assignment_group: 'network_ops',
    root_cause: null,
    rca: null,
    rca_draft: null,
    workaround: null,
    known_error_article_id: null,
    incident_ids: [],
    affected_ci_ids: [],
    change_ids: [],
    detected_by: 'ai:problem',
    created_by: 'ai:problem',
    resolution: null,
    resolved_at: null,
    closed_at: null,
    version: 2,
    created_at: '2026-10-10T09:00:00Z',
    updated_at: '2026-10-12T09:00:00Z',
    ...overrides,
  };
}
