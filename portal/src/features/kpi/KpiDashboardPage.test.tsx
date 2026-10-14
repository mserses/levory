import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import KpiDashboardPage from './KpiDashboardPage';
import { makeKpiReport } from './testData';

function mockKpi(status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/kpi'), ({ request }) => {
      const params = new URL(request.url).searchParams;
      seen.push(params);
      if (status !== 200) return HttpResponse.json({ detail: 'Sunucu hatası' }, { status });
      // kova raporları (kısa pencere) birbirinden farklı değerler döner
      const days = Number(params.get('days'));
      const main = days === 7 || days === 30 || days === 90;
      return HttpResponse.json(makeKpiReport(params.get('end') ?? undefined, main ? 1 : 0.5));
    }),
  );
  return seen;
}

describe('KpiDashboardPage (/panolar)', () => {
  it('KPI kutucukları: değer, önceki döneme göre değişim (metin + işaret), örneklem', async () => {
    const seen = mockKpi();
    renderWithProviders(<KpiDashboardPage />, { user: AGENT, route: '/panolar' });
    const mttr = await screen.findByRole('article', { name: 'Ortalama çözüm süresi (MTTR)' });
    expect(within(mttr).getByText('6 sa')).toBeInTheDocument();
    expect(within(mttr).getByText('▼ %40 önceki döneme göre (İyileşti)')).toBeInTheDocument();
    expect(within(mttr).getByText(/42 kayıt/)).toBeInTheDocument();
    const mtta = screen.getByRole('article', { name: 'Ortalama yanıt süresi (MTTA)' });
    expect(within(mtta).getByText('▲ %25 önceki döneme göre (Kötüleşti)')).toBeInTheDocument();
    const fcr = screen.getByRole('article', { name: 'İlk temasta çözüm' });
    expect(within(fcr).getByText('Önceki dönemde veri yok')).toBeInTheDocument();
    // ana rapor: varsayılan 30 gün, saate yuvarlanmış pencere sonu
    const main = seen.find((p) => p.get('days') === '30');
    expect(main?.get('end')).toMatch(/T\d\d:00:00\.000Z$/);
    expect(main?.getAll('kpi')).toContain('mttr');
  });

  it('grafikler: MTTR önceliğe göre zaman içinde, SLA uyumu, birikim yaşlanması, oranlar', async () => {
    const seen = mockKpi();
    renderWithProviders(<KpiDashboardPage />, { user: APPROVER, route: '/panolar' });
    const mttr = await screen.findByRole('figure', { name: 'MTTR — önceliğe göre' });
    await waitFor(() => {
      expect(within(mttr).getByRole('img').getAttribute('aria-label')).toContain('P1');
    });
    // 30 gün → 6 kova × 5 gün
    await waitFor(() => {
      expect(seen.filter((p) => p.get('days') === '5')).toHaveLength(6);
    });
    expect(screen.getByRole('figure', { name: 'SLA uyumu' })).toBeInTheDocument();
    const aging = screen.getByRole('figure', { name: 'Birikim yaşlanması' });
    expect(within(aging).getByRole('img').getAttribute('aria-label')).toContain(
      '30 günden fazla 1',
    );
    expect(
      screen.getByRole('figure', { name: 'Otomasyon, yapay zekâ kabulü, değişiklik başarısı' }),
    ).toBeInTheDocument();
  });

  it('pencere seçici yeni pencereyle yeniden sorgular', async () => {
    const seen = mockKpi();
    const { user } = renderWithProviders(<KpiDashboardPage />, { user: AGENT, route: '/panolar' });
    await screen.findByRole('article', { name: 'Ortalama çözüm süresi (MTTR)' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Dönem' }), '7');
    await waitFor(() => {
      expect(seen.some((p) => p.get('days') === '7')).toBe(true);
    });
    await waitFor(() => {
      expect(seen.filter((p) => p.get('days') === '1')).toHaveLength(7);
    });
  });

  it('hata durumu yeniden denenebilir', async () => {
    mockKpi(500);
    renderWithProviders(<KpiDashboardPage />, { user: AGENT, route: '/panolar' });
    expect(await screen.findByText('KPI raporu yüklenemedi')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Yeniden dene' })).toBeInTheDocument();
  });

  it('rota: requester panoyu göremez', async () => {
    renderApp({ user: REQUESTER, route: '/panolar' });
    expect(await screen.findByText('Bu sayfayı görüntüleme yetkiniz yok')).toBeInTheDocument();
  });
});
