import type {
  AIDecision,
  Change,
  ChangeApproval,
  ChangeTemplate,
  ConfigurationItem,
  FreezeWindow,
  ImpactAnalysis,
  ScheduleConflict,
} from '@/api/types';
import { http, HttpResponse } from 'msw';
import { makeDecision } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';

let seq = 0;
const nextId = (prefix: string) => `${prefix}-${String(++seq).padStart(4, '0')}`;

export function makeChange(overrides: Partial<Change> = {}): Change {
  const n = ++seq;
  return {
    id: nextId('chg'),
    number: `CHG${String(n).padStart(7, '0')}`,
    title: 'ERP veritabanı yama güncellemesi',
    description: 'Üç aylık güvenlik yaması.',
    change_type: 'normal',
    state: 'draft',
    risk_level: null,
    risk_score: null,
    implementation_plan: 'Yamayı uygula, servisleri yeniden başlat.',
    backout_plan: 'Anlık görüntüden geri dön.',
    test_plan: 'Duman testi.',
    planned_start: '2026-10-20T20:00:00Z',
    planned_end: '2026-10-20T22:00:00Z',
    actual_start: null,
    actual_end: null,
    requested_by: 'ayse',
    assignment_group: null,
    assignee: null,
    template_id: null,
    affected_ci_ids: [],
    linked_incident_ids: [],
    implementation_outcome: null,
    review_notes: null,
    closed_at: null,
    version: 1,
    created_at: '2026-10-12T09:00:00Z',
    updated_at: '2026-10-12T09:00:00Z',
    ...overrides,
  };
}

export function makeCI(overrides: Partial<ConfigurationItem> = {}): ConfigurationItem {
  return {
    id: nextId('ci'),
    name: 'erp-db-01',
    ci_class: 'database',
    environment: 'prod',
    criticality: 2,
    owner_group: null,
    status: 'active',
    description: '',
    attributes: {},
    external_refs: {},
    version: 1,
    created_at: '2026-10-01T09:00:00Z',
    updated_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}

export function makeApproval(overrides: Partial<ChangeApproval> = {}): ChangeApproval {
  return {
    id: nextId('apr'),
    change_id: 'chg',
    approver: 'kurul1',
    approver_roles: ['cab'],
    decision: 'approve',
    comment: '',
    created_at: '2026-10-13T08:00:00Z',
    ...overrides,
  };
}

export function makeTemplate(overrides: Partial<ChangeTemplate> = {}): ChangeTemplate {
  return {
    id: nextId('tpl'),
    name: 'Sertifika yenileme',
    description: 'TLS sertifikası yenileme',
    max_risk: 'low',
    allowed_ci_classes: ['application'],
    implementation_plan: 'Yeni sertifikayı yükle.',
    backout_plan: 'Eski sertifikaya dön.',
    test_plan: 'HTTPS denetimi.',
    active: true,
    created_by: 'yonetici',
    created_at: '2026-10-01T09:00:00Z',
    updated_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}

export function makeFreezeWindow(overrides: Partial<FreezeWindow> = {}): FreezeWindow {
  return {
    id: nextId('fw'),
    start_at: '2026-10-20T00:00:00Z',
    end_at: '2026-10-21T00:00:00Z',
    reason: 'Ay sonu kapanışı',
    environments: [],
    created_by: 'yonetici',
    created_at: '2026-10-01T09:00:00Z',
    ...overrides,
  };
}

export function makeImpact(
  roots: ConfigurationItem[],
  affected: { ci: ConfigurationItem; depth: number; path: string[] }[] = [],
): ImpactAnalysis {
  const all = [...roots, ...affected.map((a) => a.ci)];
  return {
    root_ci_ids: roots.map((r) => r.id),
    affected: affected.map((a) => ({ ...a, relationship: 'depends_on' as const })),
    highest_criticality: all.length ? Math.min(...all.map((c) => c.criticality)) : null,
    business_services: all.filter((c) => c.ci_class === 'service'),
    truncated: false,
  };
}

/** change_risk ajanının AIDecision çıktısı (agents/change_risk.py ile aynı şekil) */
export function makeRiskDecision(
  change: Change,
  overrides: Partial<AIDecision> = {},
  output: Record<string, unknown> = {},
): AIDecision {
  return makeDecision({
    incident_id: null,
    change_id: change.id,
    agent: 'change_risk',
    action: 'assess_risk',
    rationale: 'Üretim veritabanı etkileniyor; geri dönüş planı yeterli.',
    sources: [],
    confidence: 0.86,
    outcome: 'auto_applied',
    output: {
      proposed: { risk_score: 62, risk_level: 'high' },
      current_risk_score: 55,
      change_version: change.version,
      action_risk: 'low',
      deterministic: {
        score: 60,
        level: 'high',
        factors: {
          change_type: 15,
          criticality: 25,
          production: 10,
          business_services: 5,
          failure_history: 0,
          recent_incidents: 5,
          backout_plan: 0,
        },
        inputs: {},
        notes: [],
      },
      llm: {
        narrative: 'Üretim veritabanı etkileniyor; geri dönüş planı yeterli.',
        checklist: ['Yedeği doğrula', 'Bakım duyurusunu yayımla'],
        adjustment: 2,
        justification: 'Yoğun dönem.',
      },
      policy_reason: 'Riski yükselten öneri; güven eşiğin üstünde.',
      applied: true,
      ...output,
    },
    ...overrides,
  });
}

// --- MSW: tek değişiklik için uçlar -------------------------------------------------------

export interface ChangeApiMock {
  change: Change;
  approvals: ChangeApproval[];
  decisions: AIDecision[];
  transitions: { body: Record<string, unknown>; ifMatch: string | null }[];
  votes: Record<string, unknown>[];
  assessCalls: number;
  approvedDecisions: string[];
  /** Bir sonraki geçişte döndürülecek zamanlama çakışmaları */
  scheduleConflicts: ScheduleConflict[];
  impact: ImpactAnalysis;
}

/** GET/transition/approvals/decisions/assess/impact/conflicts uçlarını sahteler (sürüm denetimli) */
export function mockChangeApi(
  change: Change,
  opts: {
    approvals?: ChangeApproval[];
    decisions?: AIDecision[];
    assessResult?: AIDecision;
    required?: number;
  } = {},
): ChangeApiMock {
  const state: ChangeApiMock = {
    change,
    approvals: opts.approvals ?? [],
    decisions: opts.decisions ?? [],
    transitions: [],
    votes: [],
    assessCalls: 0,
    approvedDecisions: [],
    scheduleConflicts: [],
    impact: makeImpact([]),
  };
  const path = `/changes/${change.id}`;
  const required = opts.required ?? 2;
  server.use(
    http.get(apiUrl(path), () =>
      HttpResponse.json(state.change, { headers: { ETag: `"${state.change.version}"` } }),
    ),
    http.get(apiUrl(`${path}/approvals`), () => {
      const approvals = state.approvals.filter((a) => a.decision === 'approve').length;
      const rejections = state.approvals.length - approvals;
      return HttpResponse.json({
        status: {
          required,
          approvals,
          rejections,
          satisfied: approvals >= required && rejections === 0,
        },
        items: state.approvals,
      });
    }),
    http.get(apiUrl(`${path}/decisions`), () => HttpResponse.json(state.decisions)),
    http.get(apiUrl(`${path}/impact`), () => HttpResponse.json(state.impact)),
    http.get(apiUrl(`${path}/conflicts`), () => HttpResponse.json([])),
    http.post(apiUrl(`${path}/transition`), async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      const ifMatch = request.headers.get('if-match');
      state.transitions.push({ body, ifMatch });
      if (ifMatch !== `"${state.change.version}"`) {
        return HttpResponse.json(
          {
            detail: `Sürüm çakışması: beklenen ${ifMatch ?? '?'}, güncel ${state.change.version}.`,
          },
          { status: 409 },
        );
      }
      if (state.scheduleConflicts.length > 0) {
        return HttpResponse.json(
          {
            detail: {
              message: 'Planlanan pencerede çakışma var',
              conflicts: state.scheduleConflicts,
            },
          },
          { status: 409 },
        );
      }
      state.change = {
        ...state.change,
        state: body.to_state as Change['state'],
        review_notes: (body.review_notes as string | undefined) ?? state.change.review_notes,
        version: state.change.version + 1,
      };
      return HttpResponse.json(state.change);
    }),
    http.post(apiUrl(`${path}/approvals`), async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      state.votes.push(body);
      const approval = makeApproval({
        change_id: change.id,
        approver: 'kurul1',
        decision: body.decision as ChangeApproval['decision'],
        comment: (body.comment as string | undefined) ?? '',
      });
      state.approvals = [...state.approvals, approval];
      if (body.decision === 'reject') state.change = { ...state.change, state: 'rejected' };
      return HttpResponse.json(approval, { status: 201 });
    }),
    http.post(apiUrl(`${path}/assess`), () => {
      state.assessCalls += 1;
      const result = opts.assessResult ?? makeRiskDecision(state.change);
      state.decisions = [result, ...state.decisions];
      return HttpResponse.json(result);
    }),
    http.post(apiUrl('/changes/decisions/:decisionId/approve'), ({ params }) => {
      const id = String(params.decisionId);
      state.approvedDecisions.push(id);
      const decision = state.decisions.find((d) => d.id === id);
      return HttpResponse.json({ ...decision, approved_by: 'mudur' });
    }),
  );
  return state;
}
