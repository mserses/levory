import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { Improvement } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, APPROVER, renderWithProviders } from '@/test/render';
import ImprovementDetailPage from './ImprovementDetailPage';
import { KPI_DEFINITIONS, makeImprovement } from './testData';

const PATH = '/iyilestirmeler/:id';
type Call = { step: string; body: unknown; ifMatch: string | null };

function mockDetail(
  initial: Improvement,
  next: Partial<Record<string, Partial<Improvement>>> = {},
) {
  let current = initial;
  const calls: Call[] = [];
  server.use(
    http.get(apiUrl('/kpi/definitions'), () => HttpResponse.json(KPI_DEFINITIONS)),
    http.get(apiUrl('/improvements/csi-1'), () => HttpResponse.json(current)),
    http.post(apiUrl('/improvements/csi-1/:step'), async ({ request, params }) => {
      const step = String(params.step);
      const text = await request.text();
      calls.push({
        step,
        body: text ? JSON.parse(text) : null,
        ifMatch: request.headers.get('if-match'),
      });
      current = { ...current, ...(next[step] ?? {}), version: current.version + 1 };
      return HttpResponse.json(current);
    }),
  );
  return calls;
}

const render = (user = AGENT) =>
  renderWithProviders(<ImprovementDetailPage />, {
    user,
    route: '/iyilestirmeler/csi-1',
    path: PATH,
  });

describe('ImprovementDetailPage (/iyilestirmeler/:id)', () => {
  it('ölçüt (KPI + kırılım), taban/hedef/ölçülen, kanıt, bağlı kayıtlar, zaman çizelgesi', async () => {
    mockDetail(makeImprovement());
    render();
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'CSI0000001 · Ağ grubunda MTTR’yi düşür',
      }),
    ).toBeInTheDocument();
    const measure = screen.getByRole('region', { name: 'Ölçüm' });
    expect(await within(measure).findByText(/Ortalama çözüm süresi \(MTTR\)/)).toBeInTheDocument();
    expect(within(measure).getByText('10 sa')).toBeInTheDocument();
    expect(within(measure).getByText('6 sa')).toBeInTheDocument();
    expect(within(measure).getByText('Henüz ölçülmedi')).toBeInTheDocument();
    const evidence = screen.getByRole('region', { name: 'Kanıt' });
    expect(within(evidence).getByRole('link', { name: 'Bağlı sorun kaydı' })).toHaveAttribute(
      'href',
      '/problemler/prb-1',
    );
    expect(within(evidence).getByText('INC0000004, INC0000005')).toBeInTheDocument();
    const history = screen.getByRole('region', { name: 'Zaman çizelgesi' });
    expect(within(history).getAllByRole('listitem')).toHaveLength(3);
    expect(within(history).getByText(/Otomasyon \(ai:csi\)/)).toBeInTheDocument();
  });

  it('agent: uygulandı işareti (If-Match) → ölçüm penceresi gösterilir; onay/red düğmesi yok', async () => {
    const calls = mockDetail(makeImprovement(), {
      implemented: { implemented_at: '2026-09-20T09:00:00Z' },
    });
    const { user } = render();
    const actions = await screen.findByRole('group', { name: 'Durum adımları' });
    expect(within(actions).queryByRole('button', { name: 'Reddet' })).toBeNull();
    await user.click(within(actions).getByRole('button', { name: 'Uygulandı olarak işaretle' }));
    const dialog = await screen.findByRole('dialog', { name: 'Uygulandı olarak işaretle' });
    await user.click(within(dialog).getByRole('button', { name: 'Kaydet' }));
    await waitFor(() => {
      expect(calls).toEqual([{ step: 'implemented', body: { at: null }, ifMatch: '"3"' }]);
    });
    expect(await screen.findByText(/Ölçüm penceresi:/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ölç' })).toBeInTheDocument();
  });

  it('ölç: önce/sonra sonucu ve hedef durumu; approver kapatabilir', async () => {
    const calls = mockDetail(makeImprovement({ implemented_at: '2026-09-01T09:00:00Z' }), {
      measure: {
        state: 'measured',
        measured_value: 5,
        measured_at: '2026-10-05T09:00:00Z',
        target_met: true,
        measurement: {
          before: {
            value: 10,
            window: { start: '2026-08-01T00:00:00Z', end: '2026-08-31T00:00:00Z' },
          },
          after: {
            value: 5,
            count: 6,
            window: { start: '2026-09-01T09:00:00Z', end: '2026-10-01T09:00:00Z' },
          },
          delta_pct: -50,
          improved: true,
        },
      },
      close: { state: 'closed', closed_by: 'sef.ali', closed_at: '2026-10-06T09:00:00Z' },
    });
    const { user } = render(APPROVER);
    await user.click(await screen.findByRole('button', { name: 'Ölç' }));
    const dialog = await screen.findByRole('dialog', { name: 'Ölç' });
    await user.click(within(dialog).getByRole('button', { name: 'Kaydet' }));
    const measure = screen.getByRole('region', { name: 'Ölçüm' });
    expect(await within(measure).findByText('Hedef tuttu')).toBeInTheDocument();
    expect(within(measure).getByText('▼ %50 (iyileşti)')).toBeInTheDocument();
    expect(within(measure).getByText(/6 kayıt/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Kapat' }));
    const close = await screen.findByRole('dialog', { name: 'Kapat' });
    await user.click(within(close).getByRole('button', { name: 'Kaydet' }));
    await waitFor(() => {
      expect(calls.map((c) => c.step)).toEqual(['measure', 'close']);
    });
    expect(calls[1]?.body).toEqual({ note: null });
    expect(await screen.findByText('Kapandı', { selector: 'span' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Durum adımları' })).toBeNull();
  });

  it('red gerekçe ister; sürüm çakışması / pencere dolmadı mesajı', async () => {
    mockDetail(makeImprovement({ state: 'idea' }));
    server.use(
      http.post(apiUrl('/improvements/csi-1/reject'), () =>
        HttpResponse.json({ detail: 'Kayıt başka biri tarafından değiştirildi' }, { status: 409 }),
      ),
    );
    const { user } = render(APPROVER);
    const actions = await screen.findByRole('group', { name: 'Durum adımları' });
    await user.click(within(actions).getByRole('button', { name: 'Reddet' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reddet' });
    await user.click(within(dialog).getByRole('button', { name: 'Kaydet' }));
    expect(await within(dialog).findByText('Gerekçe zorunludur.')).toBeInTheDocument();
    await user.type(within(dialog).getByRole('textbox', { name: /Gerekçe/ }), 'Kapsam dışı');
    await user.click(within(dialog).getByRole('button', { name: 'Kaydet' }));
    expect(
      await within(dialog).findByText('Kayıt başka biri tarafından değiştirildi'),
    ).toBeInTheDocument();
  });

  it('bulunamadı', async () => {
    server.use(
      http.get(apiUrl('/kpi/definitions'), () => HttpResponse.json(KPI_DEFINITIONS)),
      http.get(apiUrl('/improvements/csi-1'), () =>
        HttpResponse.json({ detail: 'Yok' }, { status: 404 }),
      ),
    );
    render();
    expect(await screen.findByText('İyileştirme bulunamadı')).toBeInTheDocument();
  });
});
