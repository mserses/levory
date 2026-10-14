import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, ADMIN, renderWithProviders } from '@/test/render';
import EventGroupDetailPage from './EventGroupDetailPage';
import { makeEventGroup, makeGroupDetail } from './testData';

const PATH = '/olaylar/:id';

function mockDetail(detail = makeGroupDetail(), status = 200) {
  let current = detail;
  server.use(
    http.get(apiUrl('/events/groups/grp-1'), () =>
      status === 200
        ? HttpResponse.json(current)
        : HttpResponse.json({ detail: 'Kayıt yok' }, { status }),
    ),
  );
  return {
    set: (d: typeof detail) => {
      current = d;
    },
  };
}

describe('EventGroupDetailPage (/olaylar/:id)', () => {
  it('bağıntı: kök CI, CMDB yolu, önerilen öncelik, uyarılar', async () => {
    mockDetail();
    renderWithProviders(<EventGroupDetailPage />, {
      user: AGENT,
      route: '/olaylar/grp-1',
      path: PATH,
    });
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'EVG0000001 · db-prod-01 erişilemiyor',
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'db-prod-01' })).toHaveAttribute('href', '/cmdb/ci-db');
    const members = screen.getByRole('table', { name: 'Bağıntılı uyarılar' });
    expect(within(members).getByText('db-prod-01 → order-api')).toBeInTheDocument();
    expect(screen.getByText('P1 · Kritik')).toBeInTheDocument();
    expect(screen.getByText('Olay açılmadı')).toBeInTheDocument();
  });

  it('üstlen: R1 araç sonucu yürütüldü → grup yenilenir', async () => {
    const detail = mockDetail();
    const bodies: unknown[] = [];
    server.use(
      http.post(apiUrl('/events/groups/grp-1/acknowledge'), async ({ request }) => {
        bodies.push(await request.json());
        detail.set(
          makeGroupDetail({
            group: makeEventGroup({
              acknowledged_by: 'ayse',
              acknowledged_at: '2026-10-12T09:00:00Z',
            }),
          }),
        );
        return HttpResponse.json({
          status: 'executed',
          tool: 'event.acknowledge',
          risk_class: 'R1',
          message: '',
        });
      }),
    );
    const { user } = renderWithProviders(<EventGroupDetailPage />, {
      user: AGENT,
      route: '/olaylar/grp-1',
      path: PATH,
    });
    await user.click(await screen.findByRole('button', { name: 'Üstlen' }));
    expect(await screen.findByText('Üstlenen: ayse')).toBeInTheDocument();
    expect(bodies).toEqual([{ note: '' }]);
    expect(screen.queryByRole('button', { name: 'Üstlen' })).toBeNull();
  });

  it('sustur: gerekçe zorunlu; R2 → onay bekliyor durumu (grup susturulmaz)', async () => {
    mockDetail();
    const bodies: unknown[] = [];
    server.use(
      http.post(apiUrl('/events/groups/grp-1/silence'), async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          status: 'pending_approval',
          tool: 'event.silence',
          risk_class: 'R2',
          action_request_id: 'act-7',
          message: 'İnsan onayı bekleniyor',
        });
      }),
    );
    const { user } = renderWithProviders(<EventGroupDetailPage />, {
      user: ADMIN,
      route: '/olaylar/grp-1',
      path: PATH,
    });
    await user.click(await screen.findByRole('button', { name: 'Sustur…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Uyarı grubunu sustur' });
    await user.click(within(dialog).getByRole('button', { name: 'Onaya gönder' }));
    expect(await within(dialog).findByText('Gerekçe zorunludur.')).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Süre' }), '240');
    await user.type(within(dialog).getByRole('textbox', { name: /Gerekçe/ }), 'Bakım penceresi');
    await user.click(within(dialog).getByRole('button', { name: 'Onaya gönder' }));
    await waitFor(() => {
      expect(bodies).toEqual([{ minutes: 240, comment: 'Bakım penceresi' }]);
    });
    const status = await screen.findByRole('status', { name: 'Susturma onay bekliyor' });
    expect(status).toHaveTextContent('4 saat');
    expect(within(status).getByRole('link', { name: 'Eylem onaylarına git' })).toHaveAttribute(
      'href',
      '/onaylar?sekme=eylem',
    );
    // bekleyen istek varken ikinci susturma isteği açılmaz
    expect(screen.getByRole('button', { name: 'Sustur…' })).toBeDisabled();
  });

  it('susturulmuş ve olay bağlı grup; bekleyen olay açma önerisi', async () => {
    mockDetail(
      makeGroupDetail({
        group: makeEventGroup({
          silenced_until: '2099-01-01T00:00:00Z',
          silenced_by: 'sef.ali',
          incident_id: 'inc-9',
        }),
        pending_action_request_id: 'act-3',
      }),
    );
    renderWithProviders(<EventGroupDetailPage />, {
      user: AGENT,
      route: '/olaylar/grp-1',
      path: PATH,
    });
    expect(await screen.findByRole('link', { name: 'Olay kaydı' })).toHaveAttribute(
      'href',
      '/kayitlar/inc-9',
    );
    expect(screen.getByText(/sef\.ali tarafından susturuldu/)).toBeInTheDocument();
    expect(screen.getByText('Olay açma önerisi insan onayı bekliyor.')).toBeInTheDocument();
  });

  it('bulunamadı', async () => {
    mockDetail(makeGroupDetail(), 404);
    renderWithProviders(<EventGroupDetailPage />, {
      user: AGENT,
      route: '/olaylar/grp-1',
      path: PATH,
    });
    expect(await screen.findByText('Uyarı grubu bulunamadı')).toBeInTheDocument();
  });
});
