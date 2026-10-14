import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeArticle } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { ADMIN, AGENT, APPROVER, REQUESTER, renderApp } from '@/test/render';
import { homePathFor, navItemsFor } from './navigation';

const navLabels = async () => {
  const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
  return within(nav)
    .getAllByRole('link')
    .map((a) => a.textContent);
};

describe('navigasyon haritası', () => {
  it('rol bazlı menü', () => {
    expect(navItemsFor(['requester']).map((i) => i.path)).toEqual([
      '/talep',
      '/katalog',
      '/taleplerim',
    ]);
    expect(navItemsFor(['approver']).map((i) => i.path)).toEqual([
      '/taleplerim',
      '/kayitlar',
      '/problemler',
      '/onaylar',
      '/bilgi-bankasi',
      '/panolar',
      '/sla-tahmin',
      '/iyilestirmeler',
    ]);
    expect(navItemsFor(['admin'])).toHaveLength(15);
  });

  it('faz 4: panolar, SLA tahmini, olaylar (yalnızca agent), iyileştirmeler', () => {
    expect(navItemsFor(['agent']).map((i) => i.path)).toEqual(
      expect.arrayContaining(['/panolar', '/sla-tahmin', '/olaylar', '/iyilestirmeler']),
    );
    expect(navItemsFor(['approver']).map((i) => i.path)).not.toContain('/olaylar');
    for (const role of ['requester', 'cab', 'change_manager'] as const) {
      const paths = navItemsFor([role]).map((i) => i.path);
      expect(paths).not.toContain('/panolar');
      expect(paths).not.toContain('/olaylar');
      expect(paths).not.toContain('/iyilestirmeler');
    }
  });

  it('faz 3: katalog, sorunlar ve eylem onayları menüsü', () => {
    expect(navItemsFor(['agent']).map((i) => i.path)).toEqual(
      expect.arrayContaining(['/katalog', '/problemler']),
    );
    expect(navItemsFor(['requester']).map((i) => i.path)).not.toContain('/problemler');
    // change_manager eylem isteklerini onaylayabilir (ActionApproverUser): /onaylar görünür
    expect(navItemsFor(['change_manager']).map((i) => i.path)).toEqual([
      '/onaylar',
      '/degisiklikler',
      '/cmdb',
    ]);
    expect(navItemsFor(['cab']).map((i) => i.path)).not.toContain('/onaylar');
  });

  it('faz 2: değişiklik ve CMDB menüsü (requester ve yalnızca approver görmez)', () => {
    expect(navItemsFor(['cab']).map((i) => i.path)).toEqual(['/degisiklikler', '/cmdb']);
    expect(navItemsFor(['change_manager']).map((i) => i.path)).toContain('/degisiklikler');
    expect(navItemsFor(['change_manager']).map((i) => i.path)).toContain('/cmdb');
    expect(navItemsFor(['agent']).map((i) => i.path)).toContain('/degisiklikler');
    expect(navItemsFor(['requester']).map((i) => i.path)).not.toContain('/degisiklikler');
    expect(navItemsFor(['approver']).map((i) => i.path)).not.toContain('/cmdb');
  });

  it('açılış sayfası', () => {
    expect(homePathFor(['requester'])).toBe('/taleplerim');
    expect(homePathFor(['agent', 'approver'])).toBe('/kayitlar');
    expect(homePathFor(['approver'])).toBe('/onaylar');
    expect(homePathFor([])).toBe('/yetkisiz');
    expect(homePathFor(['cab'])).toBe('/degisiklikler');
    expect(homePathFor(['change_manager'])).toBe('/degisiklikler');
    expect(homePathFor(['requester', 'cab'])).toBe('/degisiklikler');
  });
});

describe('uygulama kabuğu', () => {
  it('requester: self-servis menüsü ve /taleplerim açılışı', async () => {
    renderApp({ user: REQUESTER, route: '/' });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Taleplerim' }),
    ).toBeInTheDocument();
    expect(await navLabels()).toEqual(['Yeni talep', 'Hizmet kataloğu', 'Taleplerim']);
    expect(screen.getByText('zeynep')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Taleplerim' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('agent: kayıtlar kuyruğuna açılır', async () => {
    renderApp({ user: AGENT, route: '/' });
    expect(await screen.findByRole('heading', { level: 1, name: 'Kayıtlar' })).toBeInTheDocument();
    expect(await navLabels()).toEqual([
      'Yeni talep',
      'Hizmet kataloğu',
      'Taleplerim',
      'Kayıtlar',
      'Sorunlar',
      'Bilgi bankası',
      'Değişiklikler',
      'CMDB',
      'Panolar',
      'SLA tahmini',
      'Olaylar',
      'İyileştirmeler',
    ]);
  });

  it('approver: onaylar', async () => {
    renderApp({ user: APPROVER, route: '/' });
    expect(await screen.findByRole('heading', { level: 1, name: 'Onaylar' })).toBeInTheDocument();
  });

  it('admin tüm ekranlara erişir', async () => {
    server.use(http.get(apiUrl('/integrations/status'), () => HttpResponse.json([])));
    renderApp({ user: ADMIN, route: '/yonetim/entegrasyonlar' });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Entegrasyonlar' }),
    ).toBeInTheDocument();
    expect(await navLabels()).toContain('Denetim');
  });

  it('requester agent ekranına giremez', async () => {
    renderApp({ user: REQUESTER, route: '/kayitlar/123' });
    expect(
      await screen.findByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
    ).toBeInTheDocument();
  });

  it('requester ve yalnızca approver değişiklik/CMDB ekranlarına giremez', async () => {
    for (const [user, route] of [
      [REQUESTER, '/degisiklikler'],
      [REQUESTER, '/degisiklikler/yeni'],
      [REQUESTER, '/cmdb'],
      [APPROVER, '/degisiklikler/c-1'],
      [APPROVER, '/cmdb/ci-1'],
    ] as const) {
      const { unmount } = renderApp({ user, route });
      expect(
        await screen.findByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
      ).toBeInTheDocument();
      unmount();
    }
  });

  it('cab: yeni değişiklik formuna giremez (oluşturma agent/change_manager)', async () => {
    renderApp({ user: { username: 'kurul', roles: ['cab'] }, route: '/degisiklikler/yeni' });
    expect(
      await screen.findByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
    ).toBeInTheDocument();
  });

  it('parametreli rota', async () => {
    server.use(
      http.get(apiUrl('/knowledge/articles/kb-9'), () =>
        HttpResponse.json(makeArticle({ id: 'kb-9', title: 'Makale kb-9' })),
      ),
    );
    renderApp({ user: AGENT, route: '/bilgi-bankasi/kb-9' });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Makale kb-9' }),
    ).toBeInTheDocument();
  });

  it('bilinmeyen sayfa', async () => {
    renderApp({ user: AGENT, route: '/yok-boyle-bir-sayfa' });
    expect(await screen.findByRole('heading', { name: 'Sayfa bulunamadı' })).toBeInTheDocument();
  });

  it('oturum yoksa dev giriş sayfasına yönlendirir ve giriş sonrası geri döner', async () => {
    const { user } = renderApp({ user: null, route: '/onaylar' });
    expect(await screen.findByRole('heading', { name: 'Giriş yap' })).toBeInTheDocument();
    await user.type(screen.getByRole('textbox', { name: /Kullanıcı adı/ }), 'sef.ali');
    await user.click(screen.getByRole('checkbox', { name: /Son kullanıcı/ }));
    await user.click(screen.getByRole('checkbox', { name: /Onaylayıcı/ }));
    await user.click(screen.getByRole('button', { name: 'Giriş yap' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Onaylar' })).toBeInTheDocument();
    expect(screen.getByText('sef.ali')).toBeInTheDocument();
  });

  it('çıkış yapınca giriş sayfası', async () => {
    const { user } = renderApp({ user: AGENT, route: '/kayitlar' });
    await screen.findByRole('heading', { level: 1, name: 'Kayıtlar' });
    await user.click(screen.getByRole('button', { name: 'Çıkış' }));
    expect(await screen.findByRole('heading', { name: 'Giriş yap' })).toBeInTheDocument();
  });

  it('sayfa değişince odak ana içeriğe taşınır (ekran okuyucu yeni sayfayı duyurur)', async () => {
    const { user } = renderApp({ user: AGENT, route: '/kayitlar' });
    await screen.findByRole('heading', { level: 1, name: 'Kayıtlar' });
    // İlk açılışta odak çalınmaz
    expect(screen.getByRole('main')).not.toHaveFocus();
    const nav = screen.getByRole('navigation', { name: 'Ana menü' });
    await user.click(within(nav).getByRole('link', { name: 'Yeni talep' }));
    await screen.findByRole('heading', { level: 1, name: /talep/i });
    expect(screen.getByRole('main')).toHaveFocus();
  });

  it('tema seçimi html data-theme özniteliğini değiştirir', async () => {
    const { user } = renderApp({ user: AGENT, route: '/kayitlar' });
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Tema' }), 'dark');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Tema' }), 'system');
    expect(document.documentElement).not.toHaveAttribute('data-theme');
  });
});
