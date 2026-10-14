import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { groupLabel } from '@/lib/taxonomy';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import ImprovementListPage from './ImprovementListPage';
import { KPI_DEFINITIONS, makeImprovement } from './testData';

function mockList(items = [makeImprovement()], status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/improvements'), ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      if (status !== 200) return HttpResponse.json({ detail: 'Sunucu hatası' }, { status });
      return HttpResponse.json(items);
    }),
    http.get(apiUrl('/kpi/definitions'), () => HttpResponse.json(KPI_DEFINITIONS)),
  );
  return seen;
}

describe('ImprovementListPage (/iyilestirmeler)', () => {
  it('CSI kaydı: durum, kaynak, KPI + kırılım, taban → hedef, ölçülen, hedef durumu', async () => {
    const seen = mockList([
      makeImprovement(),
      makeImprovement({
        id: 'csi-2',
        number: 'CSI0000002',
        title: 'İnsansız karşılamayı artır',
        state: 'closed',
        source: 'manual',
        kpi_key: 'automation_rate',
        kpi_dimension: null,
        kpi_dimension_value: null,
        baseline_value: 40,
        target_value: 60,
        measured_value: 64,
        target_met: true,
        detected_by: 'sef.ali',
        created_by: 'sef.ali',
      }),
    ]);
    renderWithProviders(<ImprovementListPage />, { user: AGENT, route: '/iyilestirmeler' });
    const table = await screen.findByRole('table', { name: 'İyileştirme kaydı' });
    expect(seen[0]?.get('limit')).toBe('100');
    const rows = within(table).getAllByRole('row').slice(1);
    const first = within(rows[0] as HTMLElement);
    expect(first.getByRole('link', { name: 'Ağ grubunda MTTR’yi düşür' })).toHaveAttribute(
      'href',
      '/iyilestirmeler/csi-1',
    );
    expect(first.getByText('Uygulanıyor')).toBeInTheDocument();
    expect(first.getByText('KPI eğilimi')).toBeInTheDocument();
    expect(first.getByText('Yapay zekâ önerisi')).toBeInTheDocument();
    expect(await first.findByText('Ortalama çözüm süresi (MTTR)')).toBeInTheDocument();
    expect(first.getByText(`Çözüm grubu: ${groupLabel('network_ops')}`)).toBeInTheDocument();
    expect(first.getByText('10 sa → 6 sa')).toBeInTheDocument();
    const second = within(rows[1] as HTMLElement);
    expect(second.getByText('Kapandı')).toBeInTheDocument();
    expect(second.getByText('%64')).toBeInTheDocument();
    expect(second.getByText('Hedef tuttu')).toBeInTheDocument();
    expect(second.queryByText('Yapay zekâ önerisi')).toBeNull();
  });

  it('durum ve kaynak süzgeci sunucuya gider', async () => {
    const seen = mockList();
    const { user } = renderWithProviders(<ImprovementListPage />, {
      user: APPROVER,
      route: '/iyilestirmeler',
    });
    await screen.findByRole('table', { name: 'İyileştirme kaydı' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Durum' }), 'measured');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Kaynak' }), 'pir');
    await waitFor(() => {
      expect(seen.at(-1)?.get('state')).toBe('measured');
      expect(seen.at(-1)?.get('source')).toBe('pir');
    });
  });

  it('boş liste', async () => {
    mockList([]);
    renderWithProviders(<ImprovementListPage />, { user: AGENT, route: '/iyilestirmeler' });
    expect(await screen.findByText('Henüz iyileştirme kaydı yok')).toBeInTheDocument();
  });

  it('hata yeniden denenebilir', async () => {
    mockList([], 500);
    renderWithProviders(<ImprovementListPage />, { user: AGENT, route: '/iyilestirmeler' });
    expect(await screen.findByText('İyileştirme kaydı yüklenemedi')).toBeInTheDocument();
  });

  it('rota: requester göremez', async () => {
    renderApp({ user: REQUESTER, route: '/iyilestirmeler' });
    expect(await screen.findByText('Bu sayfayı görüntüleme yetkiniz yok')).toBeInTheDocument();
  });
});
