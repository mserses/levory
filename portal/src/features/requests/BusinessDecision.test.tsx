import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { ServiceRequest } from '@/api/types';
import MyRequestsPage from '@/features/self-service/MyRequestsPage';
import { makeCatalogItem, makeServiceRequest } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, renderWithProviders, type TestUser } from '@/test/render';
import RequestDetailPage from './RequestDetailPage';

/** Talep edenin yöneticisi (dizinde); yalnızca requester rolüyle iş onayı verir */
const MANAGER: TestUser = { username: 'mudur.can', roles: ['requester'] };

const awaiting = (overrides: Partial<ServiceRequest> = {}) =>
  makeServiceRequest({
    number: 'REQ0000011',
    item_key: 'privileged_group_access',
    item_name: 'Yetkili grup erişimi',
    standard: false,
    requester: 'ali',
    opened_by: 'ali',
    state: 'awaiting_approval',
    approval_policy: 'manager',
    approval_state: 'pending',
    approver: 'mudur.can@ornek.com.tr',
    current_step: 0,
    step_results: [],
    touchless: null,
    fulfilled_at: null,
    closed_at: null,
    version: 2,
    can_decide: true,
    ...overrides,
  });

function mockDetail(req: ServiceRequest) {
  const posts: { path: string; body: unknown }[] = [];
  let current = req;
  server.use(
    http.get(apiUrl('/requests/:id'), () => HttpResponse.json(current)),
    http.get(apiUrl('/catalog/items/:key'), () =>
      HttpResponse.json(makeCatalogItem({ key: req.item_key })),
    ),
    http.post(apiUrl('/requests/:id/:decision'), async ({ request, params }) => {
      const body = await request.json();
      posts.push({ path: String(params.decision), body });
      current =
        params.decision === 'approve'
          ? {
              ...current,
              state: 'fulfilling',
              approval_state: 'approved',
              approved_by: 'mudur.can',
              can_decide: false,
              version: current.version + 1,
            }
          : {
              ...current,
              state: 'rejected',
              approval_state: 'rejected',
              approved_by: 'mudur.can',
              can_decide: false,
              version: current.version + 1,
            };
      return HttpResponse.json(current);
    }),
  );
  return posts;
}

const renderDetail = (req: ServiceRequest, user: TestUser = MANAGER) =>
  renderWithProviders(<RequestDetailPage />, {
    user,
    route: `/taleplerim/${req.id}`,
    path: '/taleplerim/:id',
  });

describe('Hizmet talebi iş onayı (yönetici / onay grubu)', () => {
  it('onaylayabilen kullanıcı onaylar: sürüm ve yorum gönderilir, karşılama başlar', async () => {
    const req = awaiting();
    const posts = mockDetail(req);
    const { user } = renderDetail(req);
    const panel = await screen.findByRole('region', { name: 'İş onayı' });
    expect(screen.getAllByRole('status')[0]).toHaveTextContent(/sizin iş onayınızı bekliyor/);
    await user.type(within(panel).getByLabelText(/Yorum/), 'Uygun, geçici yetki');
    await user.click(within(panel).getByRole('button', { name: 'Onayla' }));
    await waitFor(() => {
      expect(posts).toEqual([
        { path: 'approve', body: { comment: 'Uygun, geçici yetki', expected_version: 2 } },
      ]);
    });
    await waitFor(() => {
      expect(within(panel).queryByRole('button', { name: 'Onayla' })).toBeNull();
    });
    expect(panel).toHaveTextContent(/Onaylandı/);
  });

  it('ret yorum ister; yorumsuz istek gönderilmez', async () => {
    const req = awaiting();
    const posts = mockDetail(req);
    const { user } = renderDetail(req);
    const panel = await screen.findByRole('region', { name: 'İş onayı' });
    await user.click(within(panel).getByRole('button', { name: 'Reddet' }));
    expect(await within(panel).findByText(/Ret için gerekçe yazın/)).toBeInTheDocument();
    expect(posts).toEqual([]);
    await user.type(within(panel).getByLabelText(/Yorum/), 'Gerekçe yetersiz');
    await user.click(within(panel).getByRole('button', { name: 'Reddet' }));
    await waitFor(() => {
      expect(posts).toEqual([
        { path: 'reject', body: { comment: 'Gerekçe yetersiz', expected_version: 2 } },
      ]);
    });
  });

  it('karar yetkisi yoksa (can_decide=false) düğme yok', async () => {
    const req = awaiting({ can_decide: false });
    mockDetail(req);
    renderDetail(req, AGENT);
    const panel = await screen.findByRole('region', { name: 'İş onayı' });
    expect(within(panel).queryByRole('button', { name: 'Onayla' })).toBeNull();
    expect(within(panel).queryByRole('button', { name: 'Reddet' })).toBeNull();
  });

  it('409: talep değişti uyarısı ve yeniden yükleme', async () => {
    const req = awaiting();
    let gets = 0;
    server.use(
      http.get(apiUrl('/requests/:id'), () => {
        gets += 1;
        return HttpResponse.json(req);
      }),
      http.get(apiUrl('/catalog/items/:key'), () => HttpResponse.json(makeCatalogItem())),
      http.post(apiUrl('/requests/:id/approve'), () =>
        HttpResponse.json({ detail: 'REQ0000011 değişmiş' }, { status: 409 }),
      ),
    );
    const { user } = renderDetail(req);
    const panel = await screen.findByRole('region', { name: 'İş onayı' });
    await user.click(within(panel).getByRole('button', { name: 'Onayla' }));
    expect(await screen.findByText(/Talep değişti/)).toBeInTheDocument();
    await waitFor(() => {
      expect(gets).toBeGreaterThan(1);
    });
  });
});

describe('/taleplerim?sekme=onay — onayımı bekleyen talepler', () => {
  it('iş onayımı bekleyenleri ayrı sorguyla listeler', async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      http.get(apiUrl('/incidents'), () => HttpResponse.json([])),
      http.get(apiUrl('/requests'), ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return HttpResponse.json([awaiting()]);
      }),
    );
    renderWithProviders(<MyRequestsPage />, { user: MANAGER, route: '/taleplerim?sekme=onay' });
    expect(await screen.findByRole('tab', { name: 'Onayımı bekleyenler' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const table = await screen.findByRole('table', { name: /İş onayınızı bekleyen/ });
    expect(seen[0]?.get('awaiting_my_approval')).toBe('true');
    const row = within(table).getAllByRole('row')[1] as HTMLElement;
    expect(row).toHaveTextContent(/REQ0000011.*Yetkili grup erişimi.*ali/);
    expect(within(row).getByRole('link', { name: 'Yetkili grup erişimi' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^\/taleplerim\//),
    );
  });

  it('bekleyen yoksa boş durum', async () => {
    server.use(
      http.get(apiUrl('/incidents'), () => HttpResponse.json([])),
      http.get(apiUrl('/requests'), () => HttpResponse.json([])),
    );
    renderWithProviders(<MyRequestsPage />, { user: MANAGER, route: '/taleplerim?sekme=onay' });
    expect(await screen.findByText('İş onayınızı bekleyen talep yok')).toBeInTheDocument();
  });
});
