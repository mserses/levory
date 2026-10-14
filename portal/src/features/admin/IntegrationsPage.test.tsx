import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { SyncEvent, SystemStatus } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { ADMIN, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import IntegrationsPage from './IntegrationsPage';

function mockStatus(status: SystemStatus[] | Response) {
  server.use(
    http.get(apiUrl('/integrations/status'), () =>
      status instanceof Response ? status : HttpResponse.json(status),
    ),
  );
}

const BOTH: SystemStatus[] = [
  { system: 'servicenow', configured: true },
  { system: 'jira_sm', configured: false },
];

function event(overrides: Partial<SyncEvent> = {}): SyncEvent {
  return {
    id: 'ev-1',
    link_id: 'l-1',
    system: 'servicenow',
    direction: 'outbound',
    status: 'ok',
    message: 'Uzak kayıt oluşturuldu',
    payload: {},
    created_at: '2026-10-12T09:00:00Z',
    ...overrides,
  };
}

const render = () =>
  renderWithProviders(<IntegrationsPage />, { user: ADMIN, route: '/yonetim/entegrasyonlar' });

describe('IntegrationsPage', () => {
  it('sistem durumlarını gösterir; yapılandırılmamış sistemde çekme kapalı', async () => {
    mockStatus(BOTH);
    render();
    const snow = await screen.findByTestId('system-servicenow');
    const jira = screen.getByTestId('system-jira_sm');
    expect(within(snow).getByRole('heading', { name: 'ServiceNow' })).toBeInTheDocument();
    expect(within(snow).getByText('Yapılandırıldı')).toBeInTheDocument();
    expect(within(snow).getByRole('button', { name: 'ServiceNow kayıtlarını çek' })).toBeEnabled();
    expect(within(jira).getByText('Yapılandırılmamış')).toBeInTheDocument();
    expect(
      within(jira).getByRole('button', { name: 'Jira Service Management kayıtlarını çek' }),
    ).toBeDisabled();
    // Hedef seçiminde yapılandırılmamış sistem seçilemez
    expect(screen.getByRole('option', { name: /Jira Service Management/ })).toBeDisabled();
  });

  it('çekme sonucunu bildirim olarak gösterir', async () => {
    mockStatus(BOTH);
    let pulled = '';
    server.use(
      http.post(apiUrl('/integrations/:system/pull'), ({ params }) => {
        pulled = String(params.system);
        return HttpResponse.json({
          system: 'servicenow',
          cursor: 'c',
          fetched: 5,
          created: 2,
          updated: 2,
          conflicts: 1,
          skipped: 0,
          errors: 0,
        });
      }),
    );
    const { user } = render();
    await user.click(await screen.findByRole('button', { name: 'ServiceNow kayıtlarını çek' }));
    const toast = await screen.findByText('ServiceNow: 5 kayıt çekildi');
    expect(pulled).toBe('servicenow');
    expect(toast.parentElement).toHaveTextContent(
      '2 yeni · 2 güncellendi · 1 çakışma · 0 atlandı · 0 hata',
    );
    expect(toast.parentElement).toHaveTextContent('Onaylar ekranında');
  });

  it('çekme hatası (bağlayıcı hatası ve HTTP hatası) hata bildirimi olur', async () => {
    mockStatus(BOTH);
    let n = 0;
    server.use(
      http.post(apiUrl('/integrations/servicenow/pull'), () =>
        ++n === 1
          ? HttpResponse.json({ system: 'servicenow', error: 'Kimlik doğrulama reddedildi' })
          : HttpResponse.json(
              { detail: 'ServiceNow entegrasyonu yapılandırılmamış' },
              { status: 400 },
            ),
      ),
    );
    const { user } = render();
    const button = await screen.findByRole('button', { name: 'ServiceNow kayıtlarını çek' });
    await user.click(button);
    const first = await screen.findByRole('alert');
    expect(first).toHaveTextContent('ServiceNow: çekme başarısız');
    expect(first).toHaveTextContent('Kimlik doğrulama reddedildi');
    await user.click(screen.getByRole('button', { name: 'ServiceNow kayıtlarını çek' }));
    expect(
      await screen.findByText('ServiceNow entegrasyonu yapılandırılmamış'),
    ).toBeInTheDocument();
  });

  it('kaydı seçilen sisteme gönderir ve sonucu bildirir', async () => {
    mockStatus(BOTH);
    let url: URL | null = null;
    server.use(
      http.post(apiUrl('/integrations/push/:id'), ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json([event()]);
      }),
    );
    const { user } = render();
    const input = await screen.findByLabelText(/Kayıt kimliği/);
    await user.click(screen.getByRole('button', { name: 'Gönder' }));
    expect(screen.getByText('Kayıt kimliği zorunludur.')).toBeInTheDocument();
    expect(url).toBeNull();

    await user.type(input, ' inc-7 ');
    await user.selectOptions(screen.getByLabelText('Hedef sistem'), 'ServiceNow');
    await user.click(screen.getByRole('button', { name: 'Gönder' }));
    const toast = await screen.findByText('Kayıt dış sistemlere gönderildi');
    expect(toast.parentElement).toHaveTextContent('ServiceNow: Başarılı — Uzak kayıt oluşturuldu');
    expect(url!.pathname).toBe('/integrations/push/inc-7');
    expect(url!.searchParams.get('system')).toBe('servicenow');
  });

  it('gönderim: sistem seçilmezse parametresiz; hata/çakışma olayları uyarı olur', async () => {
    mockStatus(BOTH);
    let url: URL | null = null;
    server.use(
      http.post(apiUrl('/integrations/push/:id'), ({ request }) => {
        url = new URL(request.url);
        return HttpResponse.json([
          event({ status: 'error', message: 'Zaman aşımı' }),
          event({ id: 'ev-2', system: 'jira_sm', status: 'conflict', message: '' }),
        ]);
      }),
    );
    const { user } = render();
    await user.type(await screen.findByLabelText(/Kayıt kimliği/), 'inc-8');
    await user.click(screen.getByRole('button', { name: 'Gönder' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Gönderim kısmen ya da tamamen başarısız');
    expect(alert).toHaveTextContent(
      'ServiceNow: Hata — Zaman aşımı · Jira Service Management: Çakışma',
    );
    expect(url!.searchParams.has('system')).toBe(false);
  });

  it('gönderim: bulunamayan kayıt alanda, diğer hatalar bildirimde', async () => {
    mockStatus(BOTH);
    server.use(
      http.post(apiUrl('/integrations/push/:id'), ({ params }) =>
        params.id === 'yok'
          ? HttpResponse.json({ detail: 'Olay kaydı bulunamadı' }, { status: 404 })
          : HttpResponse.json(
              { detail: 'Hiçbir ITSM entegrasyonu yapılandırılmamış' },
              { status: 400 },
            ),
      ),
    );
    const { user } = render();
    const input = await screen.findByLabelText(/Kayıt kimliği/);
    await user.type(input, 'yok');
    await user.click(screen.getByRole('button', { name: 'Gönder' }));
    expect(await screen.findByText('Bu kimlikle bir kayıt bulunamadı.')).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'true');

    await user.clear(input);
    await user.type(input, 'inc-9');
    await user.click(screen.getByRole('button', { name: 'Gönder' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Gönderim yapılamadı');
    expect(alert).toHaveTextContent('Hiçbir ITSM entegrasyonu yapılandırılmamış');
  });

  it('hiç entegrasyon yoksa gönderim kapalı; durum hatası yeniden denenebilir', async () => {
    mockStatus(HttpResponse.json({ detail: 'Sunucu hatası' }, { status: 500 }));
    const { user } = render();
    expect(await screen.findByRole('heading', { name: 'Durum alınamadı' })).toBeInTheDocument();
    mockStatus([
      { system: 'servicenow', configured: false },
      { system: 'jira_sm', configured: false },
    ]);
    await user.click(screen.getByRole('button', { name: 'Yeniden dene' }));
    expect(await screen.findByRole('button', { name: 'Gönder' })).toBeDisabled();
    expect(screen.getByText(/en az bir entegrasyon yapılandırılmalı/)).toBeInTheDocument();
  });
});
