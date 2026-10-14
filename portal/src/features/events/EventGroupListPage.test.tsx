import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, renderApp, renderWithProviders } from '@/test/render';
import EventGroupListPage from './EventGroupListPage';
import { makeEventGroup } from './testData';

function mockGroups(items = [makeEventGroup()], status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/events/groups'), ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      if (status !== 200) return HttpResponse.json({ detail: 'Sunucu hatası' }, { status });
      return HttpResponse.json({ items });
    }),
    http.get(apiUrl('/cmdb/cis/:id'), ({ params }) =>
      HttpResponse.json({ id: params.id, name: params.id === 'ci-db' ? 'db-prod-01' : 'web-01' }),
    ),
  );
  return seen;
}

describe('EventGroupListPage (/olaylar)', () => {
  it('bağıntılı uyarı grupları: kök CI, önem, durum, bağlı olay', async () => {
    const seen = mockGroups([
      makeEventGroup(),
      makeEventGroup({
        id: 'grp-2',
        number: 'EVG0000002',
        title: 'web-01 disk doluyor',
        root_ci_id: 'ci-web',
        severity: 'warning',
        incident_id: 'inc-9',
        acknowledged_by: 'ayse',
        acknowledged_at: '2026-10-12T08:50:00Z',
        silenced_until: '2099-01-01T00:00:00Z',
        silenced_by: 'sef.ali',
        flapping: true,
      }),
    ]);
    renderWithProviders(<EventGroupListPage />, { user: AGENT, route: '/olaylar' });
    const table = await screen.findByRole('table', { name: 'Uyarı grupları' });
    expect(seen[0]?.get('state')).toBe('open');
    const rows = within(table).getAllByRole('row').slice(1);
    const first = within(rows[0] as HTMLElement);
    expect(first.getByRole('link', { name: 'db-prod-01 erişilemiyor' })).toHaveAttribute(
      'href',
      '/olaylar/grp-1',
    );
    expect(first.getByText('Kritik')).toBeInTheDocument();
    expect(await first.findByRole('link', { name: 'db-prod-01' })).toHaveAttribute(
      'href',
      '/cmdb/ci-db',
    );
    expect(first.getByText('Olay açılmadı')).toBeInTheDocument();
    const second = within(rows[1] as HTMLElement);
    expect(second.getByRole('link', { name: 'Olay kaydı' })).toHaveAttribute(
      'href',
      '/kayitlar/inc-9',
    );
    expect(second.getByText('Üstlenen: ayse')).toBeInTheDocument();
    expect(second.getByText(/Susturuldu/)).toBeInTheDocument();
    expect(second.getByText('Çırpınıyor')).toBeInTheDocument();
  });

  it('durum süzgeci', async () => {
    const seen = mockGroups();
    const { user } = renderWithProviders(<EventGroupListPage />, {
      user: AGENT,
      route: '/olaylar',
    });
    await screen.findByRole('table', { name: 'Uyarı grupları' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Durum' }), '');
    await waitFor(() => {
      expect(seen.at(-1)?.has('state')).toBe(false);
    });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Durum' }), 'resolved');
    await waitFor(() => {
      expect(seen.at(-1)?.get('state')).toBe('resolved');
    });
  });

  it('boş liste ve hata', async () => {
    mockGroups([]);
    renderWithProviders(<EventGroupListPage />, { user: AGENT, route: '/olaylar' });
    expect(await screen.findByText('Açık uyarı grubu yok')).toBeInTheDocument();
  });

  it('hata yeniden denenebilir', async () => {
    mockGroups([], 500);
    renderWithProviders(<EventGroupListPage />, { user: AGENT, route: '/olaylar' });
    expect(await screen.findByText('Uyarı grupları yüklenemedi')).toBeInTheDocument();
  });

  it('rota: yalnızca approver olan kullanıcı olayları göremez (GET /events AgentUser)', async () => {
    renderApp({ user: APPROVER, route: '/olaylar' });
    expect(await screen.findByText('Bu sayfayı görüntüleme yetkiniz yok')).toBeInTheDocument();
  });
});
