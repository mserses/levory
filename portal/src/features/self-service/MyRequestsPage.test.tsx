import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeIncident } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import MyRequestsPage, { MY_REQUESTS_LIMIT } from './MyRequestsPage';

const vpn = makeIncident({
  number: 'INC0000101',
  title: 'VPN bağlanmıyor',
  description: 'Satır 1\nSatır 2 <b>kalın değil</b>',
  status: 'in_progress',
  priority: 'P3',
  service: 'vpn',
  assignment_group: 'network_ops',
  responded_at: '2026-10-12T09:30:00Z',
  resolve_due_at: '2026-10-15T09:00:00Z',
  created_at: '2026-10-12T09:00:00Z',
  ai_confidence: 0.91,
});
const waiting = makeIncident({
  number: 'INC0000102',
  title: 'Yazıcı kâğıt sıkıştırıyor',
  status: 'on_hold',
  on_hold_reason: 'awaiting_caller',
  sla_paused_at: '2026-10-11T10:00:00Z',
  created_at: '2026-10-11T08:00:00Z',
});
const resolved = makeIncident({
  number: 'INC0000099',
  title: 'Outlook açılmıyor',
  status: 'resolved',
  resolution: 'Profil yeniden oluşturuldu.',
  resolved_at: '2026-10-10T12:00:00Z',
  resolve_due_at: '2026-10-11T12:00:00Z',
  created_at: '2026-10-09T08:00:00Z',
});

function mockList(data: unknown, status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/incidents'), ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      return HttpResponse.json(data as object, { status });
    }),
  );
  return seen;
}

async function setup(user = REQUESTER) {
  const utils = renderWithProviders(<MyRequestsPage />, { user, route: '/taleplerim' });
  await screen.findByRole('heading', { level: 1, name: 'Taleplerim' });
  return utils;
}

const rows = () => within(screen.getByRole('table')).getAllByRole('row').slice(1);

describe('MyRequestsPage (/taleplerim)', () => {
  it('mine=true ile kendi taleplerini listeler; varsayılan açık talepler, yeniden eskiye', async () => {
    const seen = mockList([resolved, waiting, vpn]);
    const { user } = await setup();

    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(seen[0]?.get('mine')).toBe('true');
    expect(seen[0]?.get('limit')).toBe(String(MY_REQUESTS_LIMIT));

    expect(rows().map((r) => within(r).getAllByRole('cell')[0]?.textContent)).toEqual([
      'INC0000101',
      'INC0000102',
    ]);
    const first = rows()[0];
    if (!first) throw new Error('satır yok');
    expect(within(first).getByText('Çalışılıyor')).toBeInTheDocument();
    expect(within(first).getByText('P3 · Orta')).toBeInTheDocument();
    expect(within(first).getByText('Çözüm: 15.10.2026 12:00')).toBeInTheDocument();
    expect(screen.getByText('Sizden yanıt bekleniyor')).toBeInTheDocument();
    expect(screen.getByText('SLA duraklatıldı')).toBeInTheDocument();
    expect(screen.getByText('2 / 3 talep gösteriliyor')).toBeInTheDocument();

    await user.selectOptions(screen.getByRole('combobox', { name: 'Göster' }), 'closed');
    expect(rows()).toHaveLength(1);
    expect(screen.getByText('Outlook açılmıyor')).toBeInTheDocument();
    expect(screen.getByText('SLA karşılandı')).toBeInTheDocument();

    await user.selectOptions(screen.getByRole('combobox', { name: 'Göster' }), 'all');
    expect(rows()).toHaveLength(3);
    expect(screen.getByRole('link', { name: 'Yeni talep oluştur' })).toHaveAttribute(
      'href',
      '/talep',
    );
  });

  it('ayrıntı paneli: kullanıcı bilgileri var, yapay zekâ iç ayrıntısı yok; Escape kapatır ve odağı geri verir', async () => {
    mockList([vpn]);
    const { user } = await setup();
    const trigger = await screen.findByRole('button', { name: /INC0000101 VPN bağlanmıyor/ });
    await user.click(trigger);

    const drawer = await screen.findByRole('dialog', { name: 'VPN bağlanmıyor' });
    expect(within(drawer).getByText('INC0000101')).toBeInTheDocument();
    expect(within(drawer).getByText('Uzaktan erişim VPN')).toBeInTheDocument();
    // Ham taksonomi anahtarı değil, Türkçe ad
    expect(within(drawer).getByText('Ağ operasyonları')).toBeInTheDocument();
    expect(within(drawer).queryByText('network_ops')).not.toBeInTheDocument();
    expect(within(drawer).getByText('Çözüm hedefi: 15.10.2026 12:00')).toBeInTheDocument();
    expect(
      within(drawer).getByText('Bir destek uzmanı talebiniz üzerinde çalışıyor.'),
    ).toBeInTheDocument();
    // Açıklama düz metin (HTML yorumlanmaz, satır sonları korunur)
    const desc = within(drawer).getByText(/Satır 1/);
    expect(desc).toHaveClass('prewrap');
    expect(desc.textContent).toBe('Satır 1\nSatır 2 <b>kalın değil</b>');
    expect(drawer.querySelector('b')).toBeNull();
    // AI iç ayrıntısı yok
    expect(within(drawer).queryByRole('meter')).not.toBeInTheDocument();
    expect(drawer).not.toHaveTextContent(/güven|%91|yapay zekâ/i);

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(trigger).toHaveFocus();
  });

  it('kullanıcıdan yanıt beklenen kayıtta uyarı ve çözüm notu gösterilir; kapat düğmesi çalışır', async () => {
    mockList([waiting, resolved]);
    const { user } = await setup();
    await user.click(await screen.findByRole('button', { name: /INC0000102/ }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByRole('note')).toHaveTextContent('Sizden yanıt bekleniyor.');
    await user.click(within(drawer).getByRole('button', { name: 'Ayrıntıları kapat' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.selectOptions(screen.getByRole('combobox', { name: 'Göster' }), 'closed');
    await user.click(screen.getByRole('button', { name: /INC0000099/ }));
    const resolvedDrawer = await screen.findByRole('dialog');
    expect(within(resolvedDrawer).getByText('Profil yeniden oluşturuldu.')).toBeInTheDocument();
    expect(within(resolvedDrawer).getByRole('heading', { name: 'Çözüm notu' })).toBeVisible();
  });

  it('boş liste: yeni talep çağrısı gösterilir', async () => {
    mockList([]);
    await setup();
    expect(await screen.findByRole('heading', { name: 'Henüz bir talebiniz yok' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'Yeni talep oluştur' })).toHaveAttribute(
      'href',
      '/talep',
    );
  });

  it('approver (talep açamaz): boş listede yeni talep bağlantısı yok', async () => {
    mockList([]);
    await setup(APPROVER);
    expect(await screen.findByRole('heading', { name: 'Henüz bir talebiniz yok' })).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Yeni talep oluştur' })).not.toBeInTheDocument();
  });

  it('açık talep yoksa filtre boş durumu', async () => {
    mockList([resolved]);
    await setup();
    expect(await screen.findByRole('heading', { name: 'Açık talebiniz yok' })).toBeVisible();
  });

  it('sunucu hatası: hata durumu ve yeniden deneme', async () => {
    let calls = 0;
    server.use(
      http.get(apiUrl('/incidents'), () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: 'Hizmet geçici olarak kullanılamıyor.' }, { status: 503 })
          : HttpResponse.json([vpn]);
      }),
    );
    const { user } = await setup();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('heading', { name: 'Talepleriniz yüklenemedi' })).toBeVisible();
    expect(alert).toHaveTextContent('Hizmet geçici olarak kullanılamıyor.');

    await user.click(within(alert).getByRole('button', { name: 'Yeniden dene' }));
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it('ağ hatası Türkçe mesajla gösterilir', async () => {
    server.use(http.get(apiUrl('/incidents'), () => HttpResponse.error()));
    await setup();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Sunucuya ulaşılamadı/);
  });

  it('rota tablosunda /taleplerim requester için açılır', async () => {
    mockList([vpn]);
    renderApp({ user: REQUESTER, route: '/taleplerim' });
    expect(await screen.findByRole('button', { name: /INC0000101/ })).toBeInTheDocument();
  });
});
