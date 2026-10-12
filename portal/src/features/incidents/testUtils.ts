import { http, HttpResponse } from 'msw';
import type { AIDecision, Incident } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';

/** Şimdiye göre ISO zaman (SLA rozetleri gerçek saatle hesaplanır) */
export const fromNow = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();

export interface IncidentApiMock {
  incident: Incident;
  decisions: AIDecision[];
  patches: { body: Record<string, unknown>; ifMatch: string | null }[];
  triageCalls: number;
}

/** Tek kayıt için GET/PATCH/triage/decisions uçlarını sahteler (sürüm kontrolü dahil). */
export function mockIncidentApi(
  incident: Incident,
  decisions: AIDecision[] = [],
  opts: { triageResult?: AIDecision; triageStatus?: number } = {},
): IncidentApiMock {
  const state: IncidentApiMock = { incident, decisions, patches: [], triageCalls: 0 };
  const path = `/incidents/${incident.id}`;
  server.use(
    http.get(apiUrl(path), () =>
      HttpResponse.json(state.incident, { headers: { ETag: `"${state.incident.version}"` } }),
    ),
    http.get(apiUrl(`${path}/decisions`), () => HttpResponse.json(state.decisions)),
    http.patch(apiUrl(path), async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      const ifMatch = request.headers.get('if-match');
      state.patches.push({ body, ifMatch });
      if (ifMatch !== `"${state.incident.version}"`) {
        return HttpResponse.json(
          {
            detail: `Sürüm çakışması: beklenen ${ifMatch ?? '?'}, güncel ${state.incident.version}. Kaydı yenileyip tekrar deneyin.`,
          },
          { status: 409 },
        );
      }
      state.incident = {
        ...state.incident,
        ...(body as Partial<Incident>),
        version: state.incident.version + 1,
      };
      return HttpResponse.json(state.incident);
    }),
    http.post(apiUrl(`${path}/triage`), () => {
      state.triageCalls += 1;
      if (opts.triageStatus && opts.triageStatus >= 400) {
        return HttpResponse.json(
          { detail: 'Yapay zekâ hizmeti şu anda kullanılamıyor' },
          { status: opts.triageStatus },
        );
      }
      const result = opts.triageResult;
      if (result) state.decisions = [result, ...state.decisions];
      return HttpResponse.json(result);
    }),
  );
  return state;
}
