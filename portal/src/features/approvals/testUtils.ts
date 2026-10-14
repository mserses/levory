/** Onaylar ekranı testleri için MSW kurulum yardımcıları. */
import { http, HttpResponse } from 'msw';
import type { DecisionInboxItem, Incident, SyncEvent } from '@/api/types';
import { makeIncident, makeInboxItem } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';

/** P4 → P1 yükseltme öneren, gerçek triage çıktısına benzer bir onay kutusu satırı */
export function urgentRaiseItem(overrides: Partial<DecisionInboxItem> = {}) {
  const incident = makeIncident({
    number: 'INC0000101',
    title: 'Muhasebe sunucusu yanıt vermiyor',
    impact: 'low',
    urgency: 'medium',
    category: 'sunucu',
    priority: 'P4',
    status: 'assigned',
    version: 3,
  });
  const item = makeInboxItem({
    incident_id: incident.id,
    incident_number: incident.number,
    incident_title: incident.title,
    incident_priority: 'P4',
    incident_status: 'assigned',
    incident_version: 3,
    incident_current_version: 3,
    outcome: 'needs_approval',
    confidence: 0.91,
    rationale: 'Tüm muhasebe birimi etkileniyor.\n<script>alert(1)</script>',
    sources: ['KB0000007'],
    output: {
      proposed: { impact: 'high', urgency: 'high', category: 'sunucu' },
      current_priority: 'P4',
      proposed_priority: 'P1',
      applied_fields: [],
      pending_fields: ['category', 'impact', 'urgency'],
      policy_reason: 'Öncelik P4 -> P1 yükseltmesi otomatik uygulanamaz; insan onayı gerekli.',
    },
    ...overrides,
  });
  return { item, incident };
}

export interface InboxMock {
  /** Her istekte gelen outcome değerleri */
  outcomes: string[][];
  limits: string[];
}

/**
 * GET /incidents/decisions (outcome'a göre) + GET /incidents/{id}.
 * `byOutcome` değeri bir fonksiyonsa her istekte yeniden çağrılır (onay sonrası liste değişimi için).
 */
export function mockInbox(
  byOutcome: Partial<Record<string, DecisionInboxItem[] | (() => DecisionInboxItem[] | Response)>>,
  incidents: Incident[] = [],
): InboxMock {
  const calls: InboxMock = { outcomes: [], limits: [] };
  server.use(
    http.get(apiUrl('/incidents/decisions'), ({ request }) => {
      const url = new URL(request.url);
      const outcomes = url.searchParams.getAll('outcome');
      calls.outcomes.push(outcomes);
      calls.limits.push(url.searchParams.get('limit') ?? '');
      const entry = byOutcome[outcomes[0] ?? ''];
      const result = typeof entry === 'function' ? entry() : (entry ?? []);
      return result instanceof Response ? result : HttpResponse.json(result);
    }),
    http.get(apiUrl('/incidents/:id'), ({ params }) => {
      if (params.id === 'decisions') return undefined;
      const found = incidents.find((i) => i.id === params.id);
      return found
        ? HttpResponse.json(found)
        : HttpResponse.json({ detail: 'Kayıt bulunamadı' }, { status: 404 });
    }),
  );
  return calls;
}

export function mockApprove(respond: (decisionId: string) => Response) {
  const calls: string[] = [];
  server.use(
    http.post(apiUrl('/incidents/decisions/:id/approve'), ({ params }) => {
      const id = String(params.id);
      calls.push(id);
      return respond(id);
    }),
  );
  return calls;
}

export function makeConflict(overrides: Partial<SyncEvent> = {}): SyncEvent {
  return {
    id: 'ev-1',
    link_id: 'link-1',
    system: 'servicenow',
    direction: 'inbound',
    status: 'conflict',
    message: 'Çakışma: kayıt iki tarafta da değişti, manuel çözüm gerekli',
    created_at: '2026-10-12T08:30:00Z',
    payload: {
      incident_id: 'inc-42',
      local_version: 5,
      last_synced_local_version: 4,
      remote_updated_at: '2026-10-12T08:20:00Z',
      last_synced_remote_updated: '2026-10-12T07:00:00Z',
      local: {
        title: 'Yazıcı çalışmıyor',
        description: 'Kat 3 yazıcısı kağıt sıkıştırıyor.',
        status: 'in_progress',
        priority: 'P3',
        impact: 'medium',
        urgency: 'medium',
        assignee: 'ayse',
        resolution: null,
      },
      remote: {
        external_id: 'sys-abc',
        number: 'INC0010042',
        title: 'Yazıcı çalışmıyor',
        description: 'Kat 3 yazıcısı kağıt sıkıştırıyor.',
        status: 'resolved',
        priority: 'P2',
        impact: 'medium',
        urgency: 'high',
        assignee: 'mehmet',
        updated_at: '2026-10-12T08:20:00Z',
      },
    },
    ...overrides,
  };
}
