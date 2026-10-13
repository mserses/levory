import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { changesApi } from './changes';
import { cmdbApi } from './cmdb';
import { isApiError } from './errors';

describe('changesApi', () => {
  it('liste filtreleri sorguya gider (mine, state, change_type)', async () => {
    let url: URL | null = null;
    server.use(
      http.get(apiUrl('/changes'), ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json([]);
      }),
    );
    await changesApi.list({ state: 'authorize', change_type: 'normal', mine: true });
    expect(url!.searchParams.get('state')).toBe('authorize');
    expect(url!.searchParams.get('change_type')).toBe('normal');
    expect(url!.searchParams.get('mine')).toBe('true');
  });

  it('geçiş If-Match ile sürüm gönderir', async () => {
    let ifMatch: string | null = null;
    let body: unknown = null;
    server.use(
      http.post(apiUrl('/changes/c-1/transition'), async ({ request }) => {
        ifMatch = request.headers.get('if-match');
        body = await request.json();
        return HttpResponse.json({ id: 'c-1' });
      }),
    );
    await changesApi.transition('c-1', 4, { to_state: 'assess', notes: 'hazır' });
    expect(ifMatch).toBe('"4"');
    expect(body).toEqual({ to_state: 'assess', notes: 'hazır' });
  });

  it('takvim saat dilimli from/to ile istenir', async () => {
    let url: URL | null = null;
    server.use(
      http.get(apiUrl('/changes/calendar'), ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json({ changes: [], freeze_windows: [] });
      }),
    );
    await changesApi.calendar(new Date('2026-10-01T00:00:00Z'), new Date('2026-11-01T00:00:00Z'));
    expect(url!.searchParams.get('from')).toBe('2026-10-01T00:00:00.000Z');
    expect(url!.searchParams.get('to')).toBe('2026-11-01T00:00:00.000Z');
  });

  it('çakışma 409 gövdesindeki çakışmalar okunabilir', async () => {
    server.use(
      http.post(apiUrl('/changes/c-1/transition'), () =>
        HttpResponse.json(
          {
            detail: {
              message: 'Planlanan pencerede çakışma var',
              conflicts: [
                {
                  kind: 'freeze_window',
                  ref_id: 'fw-1',
                  ref_number: null,
                  start: '2026-10-20T00:00:00Z',
                  end: '2026-10-21T00:00:00Z',
                  description: 'Yıl sonu dondurma',
                },
              ],
            },
          },
          { status: 409 },
        ),
      ),
    );
    const error = await changesApi
      .transition('c-1', 2, { to_state: 'scheduled' })
      .catch((e: unknown) => e);
    expect(isApiError(error) && error.isConflict).toBe(true);
    const { scheduleConflictsOf } = await import('./changes');
    expect(scheduleConflictsOf(error)?.map((c) => c.description)).toEqual(['Yıl sonu dondurma']);
    expect(scheduleConflictsOf(new Error('x'))).toBeNull();
  });

  it('assess ve karar onayı doğru uçlara gider', async () => {
    const calls: string[] = [];
    server.use(
      http.post(apiUrl('/changes/c-1/assess'), () => {
        calls.push('assess');
        return HttpResponse.json({ id: 'd-1' });
      }),
      http.post(apiUrl('/changes/decisions/d-1/approve'), () => {
        calls.push('approve');
        return HttpResponse.json({ id: 'd-1' });
      }),
    );
    await changesApi.assess('c-1');
    await changesApi.approveDecision('d-1');
    expect(calls).toEqual(['assess', 'approve']);
  });
});

describe('cmdbApi', () => {
  it('arama ve etki analizi', async () => {
    let listUrl: URL | null = null;
    let impactBody: unknown = null;
    server.use(
      http.get(apiUrl('/cmdb/cis'), ({ request }) => {
        listUrl = new URL(request.url);
        return HttpResponse.json([]);
      }),
      http.post(apiUrl('/cmdb/impact'), async ({ request }) => {
        impactBody = await request.json();
        return HttpResponse.json({
          root_ci_ids: [],
          affected: [],
          highest_criticality: null,
          business_services: [],
        });
      }),
    );
    await cmdbApi.list({ q: 'erp', ci_class: 'application' });
    await cmdbApi.impactFor(['a', 'b'], 3);
    expect(listUrl!.searchParams.get('q')).toBe('erp');
    expect(listUrl!.searchParams.get('ci_class')).toBe('application');
    expect(impactBody).toEqual({ ci_ids: ['a', 'b'], max_depth: 3 });
  });
});
