import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeCatalogItem } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import CatalogPage from './CatalogPage';

const items = [
  makeCatalogItem({
    key: 'password_reset',
    name: 'Şifre sıfırlama',
    description: 'Unuttuğunuz parolayı sıfırlar.',
    category: 'Kimlik ve erişim',
    standard: true,
  }),
  makeCatalogItem({
    key: 'shared_mailbox_access',
    name: 'Paylaşılan posta kutusu erişimi',
    description: 'Ekibinizin paylaşılan posta kutusuna erişim verilir.',
    category: 'E-posta ve iş birliği',
    standard: false,
    approval_policy: 'manager',
  }),
  makeCatalogItem({
    key: 'vpn_access',
    name: 'VPN erişim grubu',
    description: 'Uzaktan çalışma için VPN.',
    category: 'Ağ erişimi',
  }),
];

function mockCatalog(data: unknown = items, status = 200) {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get(apiUrl('/catalog/items'), ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      return HttpResponse.json(data as object, { status });
    }),
  );
  return seen;
}

async function setup() {
  const utils = renderWithProviders(<CatalogPage />, { user: REQUESTER, route: '/katalog' });
  await screen.findByRole('heading', { level: 1, name: 'Hizmet kataloğu' });
  return utils;
}

describe('CatalogPage (/katalog)', () => {
  it('öğeleri kategoriye göre gruplar; öğe sayfasına bağlantı ve rozetler', async () => {
    mockCatalog();
    await setup();
    await screen.findByRole('link', { name: 'Şifre sıfırlama' });
    const groups = screen
      .getAllByRole('region')
      .filter((g) => g.getAttribute('aria-label') !== 'Bildirimler');
    expect(groups.map((g) => within(g).getByRole('heading', { level: 2 }).textContent)).toEqual([
      'Ağ erişimi',
      'E-posta ve iş birliği',
      'Kimlik ve erişim',
    ]);
    const link = screen.getByRole('link', { name: 'Şifre sıfırlama' });
    expect(link).toHaveAttribute('href', '/katalog/password_reset');
    const mailbox = screen.getByRole('article', { name: 'Paylaşılan posta kutusu erişimi' });
    expect(within(mailbox).getByText('Yönetici onayı gerekir')).toBeInTheDocument();
    const reset = screen.getByRole('article', { name: 'Şifre sıfırlama' });
    expect(within(reset).getByText('Standart talep')).toBeInTheDocument();
    expect(screen.getByText('3 hizmet gösteriliyor')).toBeInTheDocument();
  });

  it('arama ad/açıklamada büyük-küçük harf ve Türkçe karakter duyarsız süzer', async () => {
    mockCatalog();
    const { user } = await setup();
    await screen.findByRole('link', { name: 'Şifre sıfırlama' });
    await user.type(screen.getByRole('searchbox', { name: 'Katalogda ara' }), 'POSTA');
    expect(screen.queryByRole('link', { name: 'Şifre sıfırlama' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Paylaşılan posta kutusu erişimi' })).toBeVisible();
    await user.clear(screen.getByRole('searchbox', { name: 'Katalogda ara' }));
    await user.type(screen.getByRole('searchbox', { name: 'Katalogda ara' }), 'şİfre');
    expect(screen.getByRole('link', { name: 'Şifre sıfırlama' })).toBeVisible();
    expect(screen.getByText('1 hizmet gösteriliyor')).toBeInTheDocument();
  });

  it('kategori seçimi listeyi daraltır; eşleşme yoksa boş durum', async () => {
    mockCatalog();
    const { user } = await setup();
    await screen.findByRole('link', { name: 'Şifre sıfırlama' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Kategori' }), 'Ağ erişimi');
    expect(screen.getAllByRole('article')).toHaveLength(1);
    await user.type(screen.getByRole('searchbox', { name: 'Katalogda ara' }), 'yok böyle');
    expect(screen.getByText('Aramanızla eşleşen hizmet yok')).toBeInTheDocument();
  });

  it('boş katalog ve hata durumları', async () => {
    mockCatalog([]);
    await setup();
    expect(await screen.findByText('Katalogda henüz hizmet yok')).toBeInTheDocument();
  });

  it('yükleme hatası yeniden denenebilir', async () => {
    mockCatalog({ detail: 'Sunucu hatası' }, 500);
    await setup();
    expect(await screen.findByText('Katalog yüklenemedi')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /yeniden dene/i })).toBeInTheDocument();
  });

  it('rota: requester kataloğa ulaşır', async () => {
    mockCatalog();
    renderApp({ user: REQUESTER, route: '/katalog' });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Hizmet kataloğu' }),
    ).toBeInTheDocument();
  });

  it('rota: yalnızca approver için yetkisiz', async () => {
    renderApp({ user: APPROVER, route: '/katalog' });
    expect(await screen.findByText('Bu sayfayı görüntüleme yetkiniz yok')).toBeInTheDocument();
  });
});
