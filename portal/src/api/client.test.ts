import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { api, buildUrl, configureApiClient, requestWithMeta } from './client';
import { ApiError, errorMessage } from './errors';
import { decisionsApi } from './decisions';
import { incidentsApi } from './incidents';

describe('buildUrl', () => {
  it('taban adres + sorgu; diziler tekrarlanır, boşlar atlanır', () => {
    expect(
      buildUrl('/incidents/decisions', {
        outcome: ['suggested', 'escalated'],
        limit: 10,
        x: undefined,
        y: '',
      }),
    ).toBe('http://api.test/incidents/decisions?outcome=suggested&outcome=escalated&limit=10');
    expect(buildUrl('incidents', { mine: true })).toBe('http://api.test/incidents?mine=true');
  });
});

describe('request', () => {
  it('bearer belirteci ve JSON gövdesini gönderir', async () => {
    configureApiClient({ getAccessToken: () => 'dev:ayse:agent' });
    let seen: { auth: string | null; body: unknown; type: string | null } | null = null;
    server.use(
      http.post(apiUrl('/incidents'), async ({ request }) => {
        seen = {
          auth: request.headers.get('authorization'),
          type: request.headers.get('content-type'),
          body: await request.json(),
        };
        return HttpResponse.json({ id: '1' }, { status: 201 });
      }),
    );
    const data = await api.post<{ id: string }>('/incidents', { title: 'x' });
    expect(data).toEqual({ id: '1' });
    expect(seen).toEqual({
      auth: 'Bearer dev:ayse:agent',
      type: 'application/json',
      body: { title: 'x' },
    });
  });

  it('belirteç yoksa Authorization göndermez', async () => {
    let auth: string | null = 'x';
    server.use(
      http.get(apiUrl('/health'), ({ request }) => {
        auth = request.headers.get('authorization');
        return HttpResponse.json({ status: 'ok' });
      }),
    );
    await api.get('/health');
    expect(auth).toBeNull();
  });

  it('sürüm If-Match olarak gider; ETag okunur', async () => {
    let ifMatch: string | null = null;
    server.use(
      http.patch(apiUrl('/incidents/abc'), ({ request }) => {
        ifMatch = request.headers.get('if-match');
        return HttpResponse.json({ version: 4 }, { headers: { ETag: '"4"' } });
      }),
    );
    await incidentsApi.update('abc', 3, { status: 'in_progress' });
    expect(ifMatch).toBe('"3"');
    const res = await requestWithMeta('PATCH', '/incidents/abc', { body: {}, ifMatch: '"7"' });
    expect(ifMatch).toBe('"7"');
    expect(res.etag).toBe('"4"');
  });

  it('409 çakışmayı ApiError olarak çağırana fırlatır', async () => {
    server.use(
      http.patch(apiUrl('/incidents/abc'), () =>
        HttpResponse.json({ detail: 'Sürüm çakışması: beklenen 3, mevcut 4' }, { status: 409 }),
      ),
    );
    const error = await incidentsApi.update('abc', 3, { title: 'y' }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(409);
    expect(apiError.isConflict).toBe(true);
    expect(apiError.needsRetriage).toBe(false);
    expect(apiError.detail).toBe('Sürüm çakışması: beklenen 3, mevcut 4');
  });

  it('bayat öneri onayında needsRetriage', async () => {
    server.use(
      http.post(apiUrl('/incidents/decisions/d1/approve'), () =>
        HttpResponse.json({ detail: 'Kayıt değişti; yeniden triage gerekli.' }, { status: 409 }),
      ),
    );
    const error = (await decisionsApi.approve('d1').catch((e: unknown) => e)) as ApiError;
    expect(error.needsRetriage).toBe(true);
  });

  it('422 doğrulama listesini alan hatalarına çevirir', async () => {
    server.use(
      http.post(apiUrl('/incidents'), () =>
        HttpResponse.json(
          {
            detail: [
              { loc: ['body', 'title'], msg: 'String should have at least 1 character', type: 'x' },
            ],
          },
          { status: 422 },
        ),
      ),
    );
    const error = (await incidentsApi.create({ title: '' }).catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(422);
    expect(error.fieldErrors).toEqual([
      { field: 'title', message: 'String should have at least 1 character' },
    ]);
    expect(error.detail).toContain('Gönderilen bilgiler doğrulanamadı');
  });

  it('gövdesiz hata için Türkçe varsayılan mesaj', async () => {
    server.use(http.get(apiUrl('/audit'), () => new HttpResponse(null, { status: 403 })));
    const error = (await api.get('/audit').catch((e: unknown) => e)) as ApiError;
    expect(error.detail).toBe('Bu işlem için yetkiniz yok.');
    server.use(http.get(apiUrl('/audit'), () => new HttpResponse('boom', { status: 502 })));
    expect(errorMessage(await api.get('/audit').catch((e: unknown) => e))).toBe(
      'Sunucuya şu anda ulaşılamıyor.',
    );
  });

  it('401 yeniden giriş kancasını çağırır ve hatayı fırlatır', async () => {
    const onUnauthorized = vi.fn();
    configureApiClient({ getAccessToken: () => 'eski', onUnauthorized });
    server.use(
      http.get(apiUrl('/me'), () => HttpResponse.json({ detail: 'Geçersiz' }, { status: 401 })),
    );
    const error = (await api.get('/me').catch((e: unknown) => e)) as ApiError;
    expect(error.isUnauthorized).toBe(true);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('ağ hatası status 0', async () => {
    server.use(http.get(apiUrl('/incidents'), () => HttpResponse.error()));
    const error = (await incidentsApi.list().catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(0);
    expect(error.isNetworkError).toBe(true);
    expect(error.detail).toMatch(/Sunucuya ulaşılamadı/);
  });

  it('listMine ?mine=true gönderir', async () => {
    let url = '';
    server.use(
      http.get(apiUrl('/incidents'), ({ request }) => {
        url = request.url;
        return HttpResponse.json([]);
      }),
    );
    await incidentsApi.listMine({ status: 'new' });
    expect(new URL(url).searchParams.get('mine')).toBe('true');
    expect(new URL(url).searchParams.get('status')).toBe('new');
  });

  it('beklenmeyen hata için genel mesaj', () => {
    expect(errorMessage(new Error('x'))).toBe('Beklenmeyen bir hata oluştu.');
  });
});
