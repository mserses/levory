import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import MyRequestsPage from '@/features/self-service/MyRequestsPage';
import { makeIncident, makeServiceRequest } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { APPROVER, REQUESTER, renderWithProviders } from '@/test/render';

const touchless = makeServiceRequest({
  number: 'REQ0000002',
  item_name: 'Şifre sıfırlama',
  created_at: '2026-10-12T09:00:00Z',
});
const waiting = makeServiceRequest({
  number: 'REQ0000003',
  item_name: 'Yetkili grup erişimi',
  state: 'fulfilling',
  touchless: null,
  fulfilled_at: null,
  closed_at: null,
  created_at: '2026-10-13T08:00:00Z',
});

function mockRequests(data: unknown, status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/requests'), ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      return HttpResponse.json(data as object, { status });
    }),
  );
  return seen;
}

describe('/taleplerim — hizmet talepleri sekmesi', () => {
  it('sekmeler: olaylar varsayılan, hizmet talepleri sekmesi kendi listesini çeker', async () => {
    server.use(
      http.get(apiUrl('/incidents'), () =>
        HttpResponse.json([makeIncident({ number: 'INC0000001', title: 'VPN bağlanmıyor' })]),
      ),
    );
    const seen = mockRequests([touchless, waiting]);
    const { user } = renderWithProviders(<MyRequestsPage />, {
      user: REQUESTER,
      route: '/taleplerim',
    });
    expect(await screen.findByRole('tab', { name: 'Olaylar' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(await screen.findByText('VPN bağlanmıyor')).toBeInTheDocument();
    expect(seen).toHaveLength(0);

    await user.click(screen.getByRole('tab', { name: 'Hizmet talepleri' }));
    const table = await screen.findByRole('table', { name: /Hizmet talepleriniz/ });
    expect(seen[0]?.get('mine')).toBe('true');
    const rows = within(table).getAllByRole('row').slice(1);
    // En yeni önce
    expect(rows.map((r) => within(r).getAllByRole('cell')[0]?.textContent)).toEqual([
      'REQ0000003',
      'REQ0000002',
    ]);
    expect(within(rows[1] as HTMLElement).getByText('✓ İnsansız çözüldü')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText('Karşılanıyor')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).queryByText(/İnsansız/)).toBeNull();
    expect(
      within(rows[1] as HTMLElement).getByRole('link', { name: 'Şifre sıfırlama' }),
    ).toHaveAttribute('href', `/taleplerim/${touchless.id}`);
    expect(screen.getByRole('link', { name: 'Katalogdan iste' })).toHaveAttribute(
      'href',
      '/katalog',
    );

    await user.selectOptions(screen.getByRole('combobox', { name: 'Göster' }), 'open');
    expect(screen.getByText('1 / 2 hizmet talebi gösteriliyor')).toBeInTheDocument();
  });

  it('boş liste kataloğa yönlendirir; onaylayıcı (katalog yetkisi yok) bağlantı görmez', async () => {
    mockRequests([]);
    renderWithProviders(<MyRequestsPage />, { user: REQUESTER, route: '/taleplerim?sekme=hizmet' });
    expect(await screen.findByText('Henüz hizmet talebiniz yok')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Kataloğa git' })).toHaveAttribute('href', '/katalog');
  });

  it('onaylayıcı için katalog bağlantısı yok', async () => {
    mockRequests([]);
    renderWithProviders(<MyRequestsPage />, { user: APPROVER, route: '/taleplerim?sekme=hizmet' });
    expect(await screen.findByText('Henüz hizmet talebiniz yok')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Katalog/ })).toBeNull();
  });

  it('hata durumu yeniden denenebilir', async () => {
    mockRequests({ detail: 'Sunucu hatası' }, 500);
    renderWithProviders(<MyRequestsPage />, { user: REQUESTER, route: '/taleplerim?sekme=hizmet' });
    expect(await screen.findByText('Hizmet talepleriniz yüklenemedi')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /yeniden dene/i })).toBeInTheDocument();
  });
});
