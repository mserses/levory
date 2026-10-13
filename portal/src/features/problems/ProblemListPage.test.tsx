import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeProblem } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import ProblemListPage from './ProblemListPage';

const vpn = makeProblem({
  number: 'PRB0000001',
  title: 'VPN ağ geçidi tekrarlayan kopma',
  incident_ids: ['i1', 'i2', 'i3'],
  priority: 'P2',
});
const human = makeProblem({
  number: 'PRB0000002',
  title: 'Yazıcı sürücüsü',
  state: 'known_error',
  detected_by: 'ayse',
  created_by: 'ayse',
});

function mockList(data: unknown = [vpn, human], status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/problems'), ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      return HttpResponse.json(data as object, { status });
    }),
  );
  return seen;
}

const cluster = {
  kind: 'ci_category',
  key: 'ci:vpn-gw|ag',
  title: 'vpn-gw-01 · Ağ',
  incident_ids: ['i4', 'i5', 'i6'],
  incident_numbers: ['INC0000004', 'INC0000005', 'INC0000006'],
  ci_id: 'ci-1',
  category: 'ag',
  size: 3,
  similarity: 1,
  confidence: 0.7,
  priority: 'P3',
};

describe('ProblemListPage (/problemler)', () => {
  it('sorunları listeler: durum, öncelik, olay sayısı, yapay zekâ tespiti', async () => {
    const seen = mockList();
    renderWithProviders(<ProblemListPage />, { user: AGENT, route: '/problemler' });
    const table = await screen.findByRole('table', { name: 'Sorun kayıtları' });
    expect(seen[0]?.get('limit')).toBe('100');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    const first = within(rows[0] as HTMLElement);
    expect(first.getByRole('link', { name: 'VPN ağ geçidi tekrarlayan kopma' })).toHaveAttribute(
      'href',
      `/problemler/${vpn.id}`,
    );
    expect(first.getByText('İnceleniyor')).toBeInTheDocument();
    expect(first.getByText('Yapay zekâ tespiti')).toBeInTheDocument();
    expect(first.getByRole('cell', { name: '3' })).toBeInTheDocument();
    const second = within(rows[1] as HTMLElement);
    expect(second.getByText('Bilinen hata')).toBeInTheDocument();
    expect(second.queryByText('Yapay zekâ tespiti')).toBeNull();
  });

  it('arama ve durum süzgeci sunucuya gider', async () => {
    const seen = mockList();
    const { user } = renderWithProviders(<ProblemListPage />, {
      user: APPROVER,
      route: '/problemler',
    });
    await screen.findByRole('table', { name: 'Sorun kayıtları' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Durum' }), 'known_error');
    await user.type(screen.getByRole('searchbox', { name: 'Numara ya da başlık' }), 'VPN');
    await user.click(screen.getByRole('button', { name: 'Ara' }));
    await waitFor(() => {
      const last = seen.at(-1);
      expect(last?.get('state')).toBe('known_error');
      expect(last?.get('q')).toBe('VPN');
    });
  });

  it('boş liste ve hata durumları', async () => {
    mockList([]);
    renderWithProviders(<ProblemListPage />, { user: AGENT, route: '/problemler' });
    expect(await screen.findByText('Henüz sorun kaydı yok')).toBeInTheDocument();
  });

  it('hata yeniden denenebilir', async () => {
    mockList({ detail: 'Sunucu hatası' }, 500);
    renderWithProviders(<ProblemListPage />, { user: AGENT, route: '/problemler' });
    expect(await screen.findByText('Sorunlar yüklenemedi')).toBeInTheDocument();
  });

  it('eğilim analizi: tara (salt okuma) → kümeler → öner → özet ve onay bağlantısı', async () => {
    mockList([]);
    const trendBodies: unknown[] = [];
    const proposeBodies: unknown[] = [];
    server.use(
      http.post(apiUrl('/problems/trends'), async ({ request }) => {
        trendBodies.push(await request.json());
        return HttpResponse.json({ window_days: 30, clusters: [cluster] });
      }),
      http.post(apiUrl('/problems/trends/propose'), async ({ request }) => {
        proposeBodies.push(await request.json());
        return HttpResponse.json({
          results: [
            {
              status: 'pending_approval',
              tool: 'problem.create',
              risk_class: 'R1',
              action_request_id: 'act-9',
              message: 'Onay bekliyor',
            },
          ],
        });
      }),
    );
    const { user } = renderWithProviders(<ProblemListPage />, {
      user: APPROVER,
      route: '/problemler',
    });
    const panel = await screen.findByRole('region', { name: 'Eğilim analizi' });
    const p = within(panel);
    await user.selectOptions(p.getByRole('combobox', { name: 'Dönem' }), '30');
    await user.click(p.getByRole('button', { name: 'Eğilimleri tara' }));
    expect(await p.findByRole('heading', { level: 3, name: 'vpn-gw-01 · Ağ' })).toBeInTheDocument();
    expect(trendBodies).toEqual([{ window_days: 30 }]);
    expect(p.getByText('Aynı CI ve kategori')).toBeInTheDocument();
    expect(p.getByText('Olaylar: INC0000004, INC0000005, INC0000006')).toBeInTheDocument();
    expect(p.getByRole('meter', { name: 'Güven' })).toHaveAttribute('aria-valuenow', '70');

    await user.click(p.getByRole('button', { name: 'Sorun kayıtları öner' }));
    expect(await p.findByText(/0 sorun kaydı açıldı, 1 öneri onay bekliyor/)).toBeInTheDocument();
    expect(proposeBodies).toEqual([{ window_days: 30 }]);
    expect(p.getByRole('link', { name: 'Eylem onaylarına git' })).toHaveAttribute(
      'href',
      '/onaylar?sekme=eylem',
    );
  });

  it('rota: requester sorunları göremez', async () => {
    renderApp({ user: REQUESTER, route: '/problemler' });
    expect(await screen.findByText('Bu sayfayı görüntüleme yetkiniz yok')).toBeInTheDocument();
  });
});
