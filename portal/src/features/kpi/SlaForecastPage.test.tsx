import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { BreachForecast, ForecastReport } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import SlaForecastPage from './SlaForecastPage';

const forecast = (over: Partial<BreachForecast>): BreachForecast => ({
  incident_id: 'inc-1',
  number: 'INC0000001',
  priority: 'P2',
  assignment_group: 'network_ops',
  status: 'in_progress',
  paused: false,
  resolve_due_at: '2026-10-12T17:00:00Z',
  probability: 0.82,
  expected_resolve_at: '2026-10-12T20:00:00Z',
  elapsed_hours: 5,
  remaining_hours: 3,
  target_hours: 8,
  basis: 'priority_group',
  sample_size: 27,
  sample_resolved: 24,
  sample_censored: 3,
  load_ratio: 1.8,
  reasons: [
    { code: 'base_rate', message: 'Benzer 27 olayın %40’ı hedefi aştı', weight: 0.4 },
    { code: 'queue_load', message: 'Grubun kuyruğu olağanın 1,8 katı', weight: 0.9 },
  ],
  ...over,
});

const report = (items: BreachForecast[]): ForecastReport => ({
  generated_at: '2026-10-12T09:00:00Z',
  lookback_days: 180,
  min_sample: 10,
  count: items.length,
  items,
});

function mockForecast(items: BreachForecast[], status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/sla/forecast'), ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      if (status !== 200) return HttpResponse.json({ detail: 'Sunucu hatası' }, { status });
      return HttpResponse.json(report(items));
    }),
  );
  return seen;
}

describe('SlaForecastPage (/sla-tahmin)', () => {
  it('olasılığa göre sıralı liste: olay bağlantısı, gerekçeler (ağırlığa göre), örneklem', async () => {
    mockForecast([
      forecast({}),
      forecast({
        incident_id: 'inc-2',
        number: 'INC0000002',
        priority: 'P4',
        probability: null,
        basis: 'none',
        sample_size: 3,
        sample_resolved: 3,
        sample_censored: 0,
        reasons: [
          {
            code: 'insufficient_sample',
            message: 'Karşılaştırma için yeterli olay yok',
            weight: 0,
          },
        ],
      }),
      forecast({
        incident_id: 'inc-3',
        number: 'INC0000003',
        probability: 1,
        basis: 'breached',
        remaining_hours: -2,
        paused: true,
      }),
    ]);
    renderWithProviders(<SlaForecastPage />, { user: AGENT, route: '/sla-tahmin' });
    const table = await screen.findByRole('table', { name: 'SLA ihlal tahmini' });
    const rows = within(table).getAllByRole('row').slice(1);
    // tahminli olanlar olasılığa göre azalan, tahminsiz sonda
    expect(rows.map((r) => within(r).getAllByRole('link')[0]?.textContent)).toEqual([
      'INC0000003',
      'INC0000001',
      'INC0000002',
    ]);
    const first = within(rows[1] as HTMLElement);
    expect(first.getByRole('link', { name: 'INC0000001' })).toHaveAttribute(
      'href',
      '/kayitlar/inc-1',
    );
    expect(first.getByText('%82')).toBeInTheDocument();
    expect(first.getByText('24 çözülmüş + 3 açık olay')).toBeInTheDocument();
    expect(first.getByText('Aynı öncelik ve grup')).toBeInTheDocument();
    const reasons = first.getAllByRole('listitem').map((li) => li.textContent);
    expect(reasons).toEqual([
      'Grubun kuyruğu olağanın 1,8 katı',
      'Benzer 27 olayın %40’ı hedefi aştı',
    ]);
    const none = within(rows[2] as HTMLElement);
    expect(none.getByText('Tahmin yok')).toBeInTheDocument();
    expect(none.getByText('Örneklem yetersiz')).toBeInTheDocument();
    const breached = within(rows[0] as HTMLElement);
    expect(breached.getByText('Hedef süre geçti')).toBeInTheDocument();
    expect(breached.getByText('SLA duraklatıldı')).toBeInTheDocument();
    expect(breached.getByText(/2 sa geçti/)).toBeInTheDocument();
  });

  it('süzgeçler sunucuya gider (grup, en düşük olasılık)', async () => {
    const seen = mockForecast([forecast({})]);
    const { user } = renderWithProviders(<SlaForecastPage />, {
      user: APPROVER,
      route: '/sla-tahmin',
    });
    await screen.findByRole('table', { name: 'SLA ihlal tahmini' });
    expect(seen[0]?.get('limit')).toBe('100');
    await user.selectOptions(screen.getByRole('combobox', { name: 'En düşük olasılık' }), '0.5');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Çözüm grubu' }), 'network_ops');
    await waitFor(() => {
      const last = seen.at(-1);
      expect(last?.get('min_probability')).toBe('0.5');
      expect(last?.get('assignment_group')).toBe('network_ops');
    });
  });

  it('boş ve hata durumları', async () => {
    mockForecast([]);
    renderWithProviders(<SlaForecastPage />, { user: AGENT, route: '/sla-tahmin' });
    expect(await screen.findByText('Tahmin edilecek açık olay yok')).toBeInTheDocument();
  });

  it('hata yeniden denenebilir', async () => {
    mockForecast([], 500);
    renderWithProviders(<SlaForecastPage />, { user: AGENT, route: '/sla-tahmin' });
    expect(await screen.findByText('SLA tahmini yüklenemedi')).toBeInTheDocument();
  });

  it('rota: requester göremez', async () => {
    renderApp({ user: REQUESTER, route: '/sla-tahmin' });
    expect(await screen.findByText('Bu sayfayı görüntüleme yetkiniz yok')).toBeInTheDocument();
  });
});
