import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { ServiceRequest } from '@/api/types';
import { makeCatalogItem, makeServiceRequest } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import RequestDetailPage from './RequestDetailPage';
import { buildTimeline } from './steps';

const item = makeCatalogItem({
  key: 'privileged_group_access',
  name: 'Yetkili grup erişimi',
  form_schema: {
    type: 'object',
    properties: {
      group: { type: 'string', format: 'group', title: 'Yetkili grup' },
      access: { type: 'string', enum: ['full_access', 'send_as'], title: 'Erişim türü' },
      remote: { type: 'boolean', title: 'Uzaktan' },
    },
  },
});

function mockRequest(req: ServiceRequest) {
  server.use(
    http.get(apiUrl('/requests/:id'), ({ params }) =>
      params.id === req.id
        ? HttpResponse.json(req)
        : HttpResponse.json({ detail: 'Talep bulunamadı' }, { status: 404 }),
    ),
    http.get(apiUrl('/catalog/items/:key'), () => HttpResponse.json(item)),
  );
}

const renderPage = (req: ServiceRequest, user = REQUESTER) =>
  renderWithProviders(<RequestDetailPage />, {
    user,
    route: `/taleplerim/${req.id}`,
    path: '/taleplerim/:id',
  });

const waitingReq = () =>
  makeServiceRequest({
    number: 'REQ0000007',
    item_key: item.key,
    item_name: 'Yetkili grup erişimi',
    standard: false,
    state: 'fulfilling',
    approval_policy: 'manager',
    approval_state: 'approved',
    approver: 'mudur.can',
    approved_by: 'mudur.can',
    approved_at: '2026-10-12T09:10:00Z',
    form_values: { group: 'sg-sunucu-yoneticileri', access: 'send_as', remote: true },
    plan: [
      { tool: 'identity.add_to_group', title: 'Yetkili gruba ekle', args: {} },
      { tool: 'notify.send_to_user', title: 'Talep edeni bilgilendir', args: {} },
      { manual: true, title: 'Erişimi doğrula' },
    ],
    current_step: 0,
    step_results: [
      {
        index: 0,
        title: 'Yetkili gruba ekle',
        tool: 'identity.add_to_group',
        risk_class: 'R3',
        status: 'pending_approval',
        action_request_id: 'act-1',
        at: '2026-10-12T09:11:00Z',
      },
    ],
    waiting_action_id: 'act-1',
    touchless: null,
    fulfilled_at: null,
    closed_at: null,
    human_approvals: 1,
    actions: [
      {
        id: 'act-1',
        number: 'ACT0000031',
        tool: 'identity.add_to_group',
        risk_class: 'R3',
        state: 'pending',
        required_approvals: 2,
        expires_at: '2026-10-13T09:11:00Z',
      },
    ],
  });

describe('RequestDetailPage (/taleplerim/:id)', () => {
  it('insansız çözülen talep: açık rozet, adımlar, kim çalıştırdı', async () => {
    const req = makeServiceRequest({ number: 'REQ0000005' });
    mockRequest(req);
    renderPage(req);
    expect(
      await screen.findByRole('heading', { level: 1, name: /REQ0000005/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('✓ İnsansız çözüldü')).toHaveAttribute('data-touchless', 'true');
    expect(screen.getByRole('status')).toHaveTextContent(/insansız çözüldü/);
    const steps = within(screen.getByRole('list', { name: 'Karşılama adımları' })).getAllByRole(
      'listitem',
    );
    expect(steps).toHaveLength(2);
    expect(within(steps[0] as HTMLElement).getByText('Tamamlandı')).toBeInTheDocument();
    expect(within(steps[0] as HTMLElement).getByText('identity.add_to_group')).toBeInTheDocument();
    expect(steps[0]).toHaveTextContent(/Çalıştıran: Otomasyon \(ai:fulfilment\)/);
    expect(steps[0]).toHaveTextContent('R1 · Düşük risk');
    // İptal yalnızca açık/başarısız taleplerde
    expect(screen.queryByRole('button', { name: 'Talebi iptal et' })).toBeNull();
  });

  it('bekleyen R3 adımı: talep eden onay kutusu bağlantısı görmez, açıklama görür', async () => {
    const req = waitingReq();
    mockRequest(req);
    renderPage(req);
    await screen.findByRole('heading', { level: 1, name: /REQ0000007/ });
    const step = screen.getAllByRole('listitem')[0] as HTMLElement;
    expect(within(step).getByText('Onay bekliyor')).toBeInTheDocument();
    expect(step).toHaveTextContent('R3 · Kritik');
    expect(step).toHaveTextContent(/ACT0000031: BT onayı bekleniyor \(2 onay gerekir/);
    expect(within(step).queryByRole('link')).toBeNull();
    // Sonraki adımlar sırada; el ile adım işaretli
    const rest = screen.getAllByRole('listitem').slice(1);
    expect(rest.map((li) => within(li).getByText(/Sırada/).textContent)).toEqual([
      'Sırada',
      'Sırada',
    ]);
    expect(rest[1]).toHaveTextContent('El ile adım (insan işi)');
    // Rozet yok (henüz karşılanmadı); iş onayı ve form değerleri etiketli
    expect(screen.queryByText(/İnsansız çözüldü/)).toBeNull();
    expect(screen.getByRole('region', { name: 'İş onayı' })).toHaveTextContent(
      /Yönetici onayı gerekir.*Onaylandı.*mudur\.can/,
    );
    const form = await screen.findByRole('region', { name: 'Form bilgileri' });
    await waitFor(() => {
      expect(form).toHaveTextContent(/Yetkili grup.*sg-sunucu-yoneticileri/);
    });
    expect(form).toHaveTextContent(/Erişim türü.*Farklı gönder/);
    expect(form).toHaveTextContent(/Uzaktan.*Evet/);
    expect(form).not.toHaveTextContent('send_as');
  });

  it('onaylayıcı bekleyen adımda eylem onayına bağlantı görür', async () => {
    const req = waitingReq();
    mockRequest(req);
    renderPage(req, APPROVER);
    const link = await screen.findByRole('link', { name: /ACT0000031 — onay kutusunda aç/ });
    expect(link).toHaveAttribute('href', '/onaylar?sekme=eylem&istek=act-1');
  });

  it('insan katkısıyla karşılanan talep insansız sayılmaz; neden gösterilir', async () => {
    const req = makeServiceRequest({
      touchless: false,
      human_approvals: 1,
      touchless_reason: 'eylem isteği insan onayı: sef.ali (ACT0000031 identity.add_to_group)',
    });
    mockRequest(req);
    renderPage(req);
    expect(await screen.findByText('İnsan katkısıyla çözüldü')).toBeInTheDocument();
    expect(screen.queryByText('✓ İnsansız çözüldü')).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent(/sef\.ali/);
  });

  it('başarısız adım hata metni; talep eden iptal edebilir (sürümlü)', async () => {
    const req = makeServiceRequest({
      number: 'REQ0000009',
      state: 'failed',
      touchless: false,
      error: 'Adım 1 (Yazılım grubuna ekle): Dizin erişilemiyor',
      step_results: [
        {
          index: 0,
          title: 'Yazılım grubuna ekle',
          tool: 'identity.add_to_group',
          status: 'failed',
          error: { code: 'unavailable', message: 'Dizin erişilemiyor' },
          at: '2026-10-12T09:00:05Z',
        },
      ],
    });
    mockRequest(req);
    const bodies: unknown[] = [];
    server.use(
      http.post(apiUrl(`/requests/${req.id}/cancel`), async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...req, state: 'cancelled', version: req.version + 1 });
      }),
    );
    const { user } = renderPage(req);
    await screen.findByRole('heading', { level: 1, name: /REQ0000009/ });
    expect(screen.getAllByText('Dizin erişilemiyor').length).toBeGreaterThan(0);
    expect(screen.getByRole('alert')).toHaveTextContent('Adım 1 (Yazılım grubuna ekle)');
    // Sonucu olmayan adım: talep bittiyse "Çalışmadı"
    expect(screen.getAllByRole('listitem')[1]).toHaveTextContent('Çalışmadı');

    await user.click(screen.getByRole('button', { name: 'Talebi iptal et' }));
    const dialog = await screen.findByRole('alertdialog');
    await user.type(within(dialog).getByRole('textbox', { name: /Not/ }), 'Artık gerekmiyor');
    await user.click(within(dialog).getByRole('button', { name: 'İptal et' }));
    await waitFor(() => {
      expect(bodies).toEqual([{ note: 'Artık gerekmiyor', expected_version: req.version }]);
    });
    expect(await screen.findByText('İptal edildi')).toBeInTheDocument();
  });

  it('başkasının talebi (404) bulunamadı gösterir', async () => {
    mockRequest(makeServiceRequest());
    renderWithProviders(<RequestDetailPage />, {
      user: REQUESTER,
      route: '/taleplerim/baskasinin',
      path: '/taleplerim/:id',
    });
    expect(await screen.findByRole('heading', { name: 'Talep bulunamadı' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Taleplerime dön' })).toHaveAttribute(
      'href',
      '/taleplerim?sekme=hizmet',
    );
  });

  it('rota: agent talep ayrıntısını açar', async () => {
    const req = makeServiceRequest({ number: 'REQ0000011' });
    mockRequest(req);
    renderApp({ user: AGENT, route: `/taleplerim/${req.id}` });
    expect(
      await screen.findByRole('heading', { level: 1, name: /REQ0000011/ }),
    ).toBeInTheDocument();
  });
});

describe('buildTimeline', () => {
  it('bilinmeyen durum ve eksik başlık güvenli varsayılana düşer', () => {
    const req = makeServiceRequest({
      state: 'fulfilling',
      plan: [{ tool: 'x.y' }, { manual: true, title: 'El ile' }],
      step_results: [{ index: 0, status: 'tuhaf', risk_class: 'R9' }],
    });
    const steps = buildTimeline(req);
    expect(steps[0]).toMatchObject({
      title: 'Adım 1',
      tool: 'x.y',
      state: 'queued',
      riskClass: null,
    });
    expect(steps[1]).toMatchObject({ manual: true, tool: null, state: 'queued' });
  });
});
