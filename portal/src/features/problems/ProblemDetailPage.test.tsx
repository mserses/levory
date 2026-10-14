import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { Problem } from '@/api/types';
import { makeIncident, makeProblem } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, renderWithProviders } from '@/test/render';
import ProblemDetailPage from './ProblemDetailPage';
import { draftToRca, rcaToDraft, validateRca } from './model';

const inc1 = makeIncident({
  id: 'i1',
  number: 'INC0000101',
  title: 'VPN koptu (Ankara)',
  category: 'ag',
  created_at: '2026-10-01T08:00:00Z',
});
const inc2 = makeIncident({
  id: 'i2',
  number: 'INC0000102',
  title: 'VPN koptu (İzmir)',
  category: 'ag',
  created_at: '2026-10-05T08:00:00Z',
});

const base = () =>
  makeProblem({
    id: 'p1',
    number: 'PRB0000001',
    title: 'VPN ağ geçidi kopması',
    description: 'Eğilim analizi: son 14 günde 2 olay (aynı CI ve kategori).',
    incident_ids: ['i1', 'i2'],
    affected_ci_ids: ['ci-1'],
    version: 2,
    rca_draft: {
      method: '5_why',
      steps: [{ question: 'Neden koptu?', answer: 'Oturum zaman aşımı' }],
      root_cause: 'Yanlış zaman aşımı ayarı',
    },
  });

interface Calls {
  method: string;
  path: string;
  ifMatch: string | null;
  body: unknown;
}

function mockProblem(
  initial: Problem,
  respond: Partial<Record<string, (c: Calls) => Response>> = {},
) {
  let current = initial;
  const calls: Calls[] = [];
  const record = async (request: Request, path: string) => {
    const text = await request.text();
    const call = {
      method: request.method,
      path,
      ifMatch: request.headers.get('if-match'),
      body: text ? (JSON.parse(text) as unknown) : null,
    };
    calls.push(call);
    return call;
  };
  const ok = (patch: Partial<Problem>) => {
    current = { ...current, ...patch, version: current.version + 1 };
    return HttpResponse.json(current);
  };
  server.use(
    http.get(apiUrl('/problems/:id'), ({ params }) =>
      params.id === current.id
        ? HttpResponse.json(current)
        : HttpResponse.json({ detail: 'Sorun bulunamadı' }, { status: 404 }),
    ),
    http.get(apiUrl('/incidents/:id'), ({ params }) => {
      const found = [inc1, inc2].find((i) => i.id === params.id);
      return found ? HttpResponse.json(found) : HttpResponse.json({}, { status: 404 });
    }),
    http.patch(apiUrl('/problems/:id'), async ({ request }) => {
      const c = await record(request, 'patch');
      return respond.patch?.(c) ?? ok(c.body as Partial<Problem>);
    }),
    http.delete(apiUrl('/problems/:id/incidents/:incident'), async ({ request, params }) => {
      await record(request, `unlink:${String(params.incident)}`);
      return ok({ incident_ids: current.incident_ids.filter((i) => i !== params.incident) });
    }),
    http.post(apiUrl('/problems/:id/known-error/publish'), async ({ request }) => {
      await record(request, 'publish');
      return ok({ state: 'known_error', known_error_article_id: 'kb-9' });
    }),
    http.put(apiUrl('/problems/:id/rca'), async ({ request }) => {
      const c = await record(request, 'rca');
      return ok({ rca: (c.body as { rca: Record<string, unknown> }).rca });
    }),
    http.post(apiUrl('/problems/:id/rca/accept'), async ({ request }) => {
      await record(request, 'accept');
      return ok({ rca: current.rca_draft, root_cause: 'Yanlış zaman aşımı ayarı' });
    }),
    http.post(apiUrl('/problems/:id/rca/draft'), async ({ request }) => {
      const c = await record(request, 'draft');
      return (
        respond.draft?.(c) ??
        HttpResponse.json({
          status: 'pending_approval',
          tool: 'problem.record_rca',
          risk_class: 'R1',
          action_request_id: 'act-5',
          message: 'Gölge mod açık; öneri onaya gönderildi.',
        })
      );
    }),
    http.post(apiUrl('/problems/:id/fix'), async ({ request }) => {
      await record(request, 'fix');
      current = { ...current, change_ids: ['chg-7'], version: current.version + 1 };
      return HttpResponse.json({
        status: 'executed',
        tool: 'problem.propose_fix',
        risk_class: 'R1',
        result: { problem: current, change: { id: 'chg-7', number: 'CHG0000007' } },
      });
    }),
    http.post(apiUrl('/problems/:id/transition'), async ({ request }) => {
      const c = await record(request, 'transition');
      return ok({ state: (c.body as { to_state: Problem['state'] }).to_state });
    }),
  );
  return calls;
}

const renderPage = (user = AGENT, id = 'p1') =>
  renderWithProviders(<ProblemDetailPage />, {
    user,
    route: `/problemler/${id}`,
    path: '/problemler/:id',
  });

describe('ProblemDetailPage (/problemler/:id)', () => {
  it('başlık, eğilim kanıtı ve bağlı olaylar; bağ kaldırma', async () => {
    const calls = mockProblem(base());
    const { user } = renderPage();
    expect(
      await screen.findByRole('heading', { level: 1, name: /PRB0000001 · VPN ağ geçidi/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Yapay zekâ tespiti').length).toBeGreaterThan(0);
    const evidence = screen.getByRole('region', { name: 'Eğilim kanıtı ve bağlı olaylar' });
    expect(evidence).toHaveTextContent('Eğilim analizi: son 14 günde 2 olay');
    const link = await within(evidence).findByRole('link', { name: 'VPN koptu (Ankara)' });
    expect(link).toHaveAttribute('href', '/kayitlar/i1');
    await waitFor(() => {
      expect(within(evidence).getByTestId('evidence-summary')).toHaveTextContent(
        /2 bağlı olay · 1 Ekim 2026 – 5 Ekim 2026 · ortak kategori: Ağ ve bağlantı · 1 etkilenen CI/,
      );
    });
    await user.click(within(evidence).getByRole('button', { name: 'INC0000101 bağını kaldır' }));
    await waitFor(() => {
      expect(calls.map((c) => c.path)).toContain('unlink:i1');
    });
    await waitFor(() => {
      expect(within(evidence).queryByText('VPN koptu (Ankara)')).toBeNull();
    });
  });

  it('geçici çözüm If-Match ile kaydedilir, sonra bilinen hata olarak yayımlanır', async () => {
    const calls = mockProblem(base());
    const { user } = renderPage();
    const section = await screen.findByRole('region', { name: 'Geçici çözüm ve bilinen hata' });
    const s = within(section);
    expect(s.getByRole('button', { name: 'Bilinen hata olarak yayımla' })).toBeDisabled();
    await user.type(
      s.getByRole('textbox', { name: /Geçici çözüm/ }),
      'VPN istemcisini yeniden başlatın.',
    );
    expect(s.getByText('Yayımlamadan önce geçici çözümü kaydedin.')).toBeInTheDocument();
    await user.click(s.getByRole('button', { name: 'Geçici çözümü kaydet' }));
    await waitFor(() => {
      expect(calls[0]).toMatchObject({
        path: 'patch',
        ifMatch: '"2"',
        body: { workaround: 'VPN istemcisini yeniden başlatın.' },
      });
    });
    await user.click(await s.findByRole('button', { name: 'Bilinen hata olarak yayımla' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent(/insan onayıyla \(R2\)/);
    await user.click(within(dialog).getByRole('button', { name: 'Yayımla' }));
    expect(await s.findByRole('link', { name: 'bilgi bankasında aç' })).toHaveAttribute(
      'href',
      '/bilgi-bankasi/kb-9',
    );
    expect(calls.map((c) => c.path)).toEqual(['patch', 'publish']);
    expect(screen.getAllByText('Bilinen hata').length).toBeGreaterThan(0);
  });

  it('sürüm çakışması (409) bildirilir ve kayıt yeniden yüklenir', async () => {
    mockProblem(base(), {
      patch: () => HttpResponse.json({ detail: 'Sürüm çakışması' }, { status: 409 }),
    });
    const { user } = renderPage();
    const section = await screen.findByRole('region', { name: 'Geçici çözüm ve bilinen hata' });
    await user.type(within(section).getByRole('textbox', { name: /Geçici çözüm/ }), 'x');
    await user.click(within(section).getByRole('button', { name: 'Geçici çözümü kaydet' }));
    expect(
      await screen.findByText('Sorun kaydı başkası tarafından değiştirildi'),
    ).toBeInTheDocument();
  });

  it('RCA düzenleyici: yapay zekâ taslağıyla dolu; doğrulama; kesin RCA PUT gövdesi', async () => {
    const calls = mockProblem(base());
    const { user } = renderPage();
    const section = await screen.findByRole('region', { name: 'Kök neden analizi' });
    const s = within(section);
    expect(s.getByText('Yapay zekâ taslağı')).toBeInTheDocument();
    expect(s.getByText('Henüz kesin kök neden analizi yok.')).toBeInTheDocument();
    expect(s.getByRole('textbox', { name: 'Adım 1 — Neden?' })).toHaveValue('Neden koptu?');

    await user.click(s.getByRole('button', { name: 'Adım ekle' }));
    await user.click(s.getByRole('button', { name: 'Kesin RCA olarak kaydet' }));
    const alert = await s.findByRole('alert');
    expect(alert).toHaveTextContent('Adım 2: yanıt zorunludur.');
    expect(alert).toHaveFocus();
    await user.type(s.getByRole('textbox', { name: 'Adım 2 — Neden?' }), 'Neden ayar yanlış?');
    await user.type(s.getByRole('textbox', { name: 'Adım 2 — Yanıt' }), 'Varsayılan bırakılmış');
    await user.click(s.getByRole('button', { name: 'Kesin RCA olarak kaydet' }));
    await waitFor(() => {
      expect(calls.find((c) => c.path === 'rca')).toMatchObject({
        ifMatch: '"2"',
        body: {
          rca: {
            method: '5_why',
            steps: [
              { question: 'Neden koptu?', answer: 'Oturum zaman aşımı' },
              { question: 'Neden ayar yanlış?', answer: 'Varsayılan bırakılmış' },
            ],
            root_cause: 'Yanlış zaman aşımı ayarı',
          },
        },
      });
    });
    expect(await s.findByTestId('final-rca')).toHaveTextContent('Varsayılan bırakılmış');
  });

  it('taslağı kabul et ve yapay zekâ taslağı (onaya düştü → eylem bağlantısı)', async () => {
    const calls = mockProblem(base());
    const { user } = renderPage(APPROVER);
    const section = await screen.findByRole('region', { name: 'Kök neden analizi' });
    const s = within(section);
    await user.click(s.getByRole('button', { name: 'Yapay zekâ taslağı üret' }));
    expect(await s.findByText(/Taslak isteği insan onayına düştü/)).toBeInTheDocument();
    expect(s.getByRole('link', { name: 'Eylem onayını aç' })).toHaveAttribute(
      'href',
      '/onaylar?sekme=eylem&istek=act-5',
    );
    await user.click(s.getByRole('button', { name: 'Taslağı olduğu gibi kabul et' }));
    expect(await s.findByTestId('final-rca')).toHaveTextContent('Oturum zaman aşımı');
    expect(calls.map((c) => c.path)).toEqual(['draft', 'accept']);
  });

  it('kalıcı çözüm önerisi taslak değişikliğe bağlantı verir', async () => {
    const calls = mockProblem(base());
    const { user } = renderPage();
    const section = await screen.findByRole('region', { name: 'Kalıcı çözüm' });
    const s = within(section);
    expect(s.getByText('Bağlı değişiklik (RFC) yok.')).toBeInTheDocument();
    await user.click(s.getByRole('button', { name: 'Kalıcı çözüm öner (taslak RFC)' }));
    const dialog = await screen.findByRole('dialog', { name: 'Kalıcı çözüm öner' });
    await user.type(
      within(dialog).getByRole('textbox', { name: /Uygulama planı/ }),
      'Zaman aşımını 8 saate çıkar',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Taslak RFC öner' }));
    expect(await s.findByRole('link', { name: 'CHG0000007' })).toHaveAttribute(
      'href',
      '/degisiklikler/chg-7',
    );
    expect(calls.find((c) => c.path === 'fix')?.body).toEqual({
      implementation_plan: 'Zaman aşımını 8 saate çıkar',
    });
    expect(await s.findByRole('link', { name: 'Değişiklik 1' })).toHaveAttribute(
      'href',
      '/degisiklikler/chg-7',
    );
  });

  it('durum geçişi: çözülmemiş sorunu kapatmak not ister', async () => {
    const calls = mockProblem(base());
    const { user } = renderPage();
    const section = await screen.findByRole('region', { name: 'Durum' });
    const s = within(section);
    const select = s.getByRole('combobox', { name: 'Yeni durum' });
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Seçiniz', 'Bilinen hata', 'Çözüldü', 'Kapandı']);
    await user.selectOptions(select, 'closed');
    await user.click(s.getByRole('button', { name: 'Durumu değiştir' }));
    expect(s.getByText('Kapanış notu zorunludur.')).toBeInTheDocument();
    await user.type(s.getByRole('textbox', { name: /Not/ }), 'Yinelenen kayıt');
    await user.click(s.getByRole('button', { name: 'Durumu değiştir' }));
    await waitFor(() => {
      expect(calls.find((c) => c.path === 'transition')).toMatchObject({
        ifMatch: '"2"',
        body: { to_state: 'closed', note: 'Yinelenen kayıt' },
      });
    });
    // Kapalı sorunda düzenleme yok
    await waitFor(() => {
      expect(screen.queryByRole('region', { name: 'Durum' })).toBeNull();
    });
    expect(screen.queryByRole('button', { name: 'Kesin RCA olarak kaydet' })).toBeNull();
  });

  it('bulunamayan sorun', async () => {
    mockProblem(base());
    renderPage(AGENT, 'yok');
    expect(await screen.findByRole('heading', { name: 'Sorun bulunamadı' })).toBeInTheDocument();
  });
});

describe('RCA modeli', () => {
  it('zaman çizelgesi zaman ister; ISO saate çevrilir', () => {
    const d = rcaToDraft({
      method: 'timeline',
      steps: [{ answer: 'Alarm', at: '2026-10-01T07:00:00Z' }],
    });
    expect(d.steps[0]?.at).toBe('2026-10-01T10:00');
    expect(validateRca(d)).toEqual([]);
    expect(draftToRca(d)).toEqual({
      method: 'timeline',
      steps: [{ answer: 'Alarm', at: '2026-10-01T07:00:00.000Z' }],
      root_cause: null,
    });
    const empty = rcaToDraft(null);
    expect(empty.method).toBe('5_why');
    expect(validateRca(empty)).toEqual([
      'Adım 1: yanıt zorunludur.',
      'Adım 1: 5 Neden adımında soru zorunludur.',
    ]);
  });
});
