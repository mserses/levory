import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { makeArticle } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { ADMIN, AGENT, APPROVER, REQUESTER, renderApp } from '@/test/render';
import { COMPACT_QUERY } from './AppShell';
import { NAV_GROUPS, homePathFor, navGroupsFor, navItemsFor } from './navigation';

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

  it('menü grupları sabit sırada; her öğe bir gruba ait, boş gruplar gizlenir', () => {
    const groupLabels = (roles: string[]) => navGroupsFor(roles).map((g) => g.label);
    expect(NAV_GROUPS.map((g) => g.label)).toEqual([
      'Self-servis',
      'Hizmet operasyonu',
      'Değişiklik ve yapılandırma',
      'Bilgi',
      'Performans',
      'Yönetim',
    ]);
    expect(navGroupsFor(['admin']).map((g) => [g.label, g.items.map((i) => i.label)])).toEqual([
      ['Self-servis', ['Yeni talep', 'Hizmet kataloğu', 'Taleplerim']],
      ['Hizmet operasyonu', ['Kayıtlar', 'Sorunlar', 'Olaylar', 'Onaylar']],
      ['Değişiklik ve yapılandırma', ['Değişiklikler', 'CMDB']],
      ['Bilgi', ['Bilgi bankası']],
      ['Performans', ['Panolar', 'SLA tahmini', 'İyileştirmeler']],
      ['Yönetim', ['Denetim', 'Entegrasyonlar']],
    ]);
    expect(groupLabels(['requester'])).toEqual(['Self-servis']);
    expect(groupLabels(['cab'])).toEqual(['Değişiklik ve yapılandırma']);
    expect(groupLabels(['change_manager'])).toEqual([
      'Hizmet operasyonu',
      'Değişiklik ve yapılandırma',
    ]);
    expect(groupLabels(['approver'])).toEqual([
      'Self-servis',
      'Hizmet operasyonu',
      'Bilgi',
      'Performans',
    ]);
    expect(groupLabels([])).toEqual([]);
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
      'Olaylar',
      'Değişiklikler',
      'CMDB',
      'Bilgi bankası',
      'Panolar',
      'SLA tahmini',
      'İyileştirmeler',
    ]);
  });

  it('menü grupları role göre çizilir (boş grup başlığı yok)', async () => {
    renderApp({ user: REQUESTER, route: '/taleplerim' });
    const nav = await screen.findByRole('navigation', { name: 'Ana menü' });
    expect(within(nav).getAllByRole('group')).toHaveLength(1);
    expect(within(nav).getByRole('group', { name: 'Self-servis' })).toBeInTheDocument();
    expect(within(nav).queryByText('Yönetim')).not.toBeInTheDocument();
  });

  it('admin+agent: tüm grup başlıkları ve etkin bağlantı vurgusu', async () => {
    server.use(http.get(apiUrl('/integrations/status'), () => HttpResponse.json([])));
    renderApp({
      user: { username: 'tam', roles: ['agent', 'admin'] },
      route: '/yonetim/entegrasyonlar',
    });
    await screen.findByRole('heading', { level: 1, name: 'Entegrasyonlar' });
    const nav = screen.getByRole('navigation', { name: 'Ana menü' });
    const admin = within(nav).getByRole('group', { name: 'Yönetim' });
    expect(within(admin).getByRole('link', { name: 'Entegrasyonlar' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(admin).getByRole('link', { name: 'Denetim' })).not.toHaveAttribute(
      'aria-current',
    );
    expect(
      within(nav)
        .getAllByRole('group')
        .map((g) => g.firstChild?.textContent),
    ).toEqual(NAV_GROUPS.map((g) => g.label));
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

describe('dar ekran çekmece menüsü', () => {
  function mockCompact(matches: boolean) {
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => ({
        matches: query === COMPACT_QUERY ? matches : false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
  }
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('Menü butonu çekmeceyi açar: odak etkin bağlantıda, kaydırma kilitli; Escape kapatır, odak butona döner', async () => {
    mockCompact(true);
    const { user } = renderApp({ user: AGENT, route: '/kayitlar' });
    await screen.findByRole('heading', { level: 1, name: 'Kayıtlar' });
    const button = screen.getByRole('button', { name: 'Menü' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('dialog', { name: 'Menü' })).not.toBeInTheDocument();

    await user.click(button);
    const drawer = screen.getByRole('dialog', { name: 'Menü' });
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(drawer).toHaveAttribute('aria-modal', 'true');
    expect(within(drawer).getByRole('link', { name: 'Kayıtlar' })).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    // Kullanıcı bilgisi, tema ve çıkış dar ekranda çekmecede
    expect(within(drawer).getByRole('combobox', { name: 'Tema' })).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: 'Çıkış' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Menü' })).not.toBeInTheDocument();
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(button).toHaveFocus();
    expect(document.body.style.overflow).toBe('');
  });

  it('odak çekmecede tutulur; kapat butonu ve arka plan kapatır', async () => {
    mockCompact(true);
    const { user } = renderApp({ user: REQUESTER, route: '/taleplerim' });
    await screen.findByRole('heading', { level: 1, name: 'Taleplerim' });
    const button = screen.getByRole('button', { name: 'Menü' });
    await user.click(button);
    const drawer = screen.getByRole('dialog', { name: 'Menü' });
    // Son öğeden (Çıkış) Tab: başa (Menüyü kapat) döner
    within(drawer).getByRole('button', { name: 'Çıkış' }).focus();
    await user.tab();
    expect(within(drawer).getByRole('button', { name: 'Menüyü kapat' })).toHaveFocus();
    await user.tab({ shift: true });
    expect(within(drawer).getByRole('button', { name: 'Çıkış' })).toHaveFocus();

    await user.click(within(drawer).getByRole('button', { name: 'Menüyü kapat' }));
    expect(screen.queryByRole('dialog', { name: 'Menü' })).not.toBeInTheDocument();
    expect(button).toHaveFocus();

    await user.click(button);
    await user.click(screen.getByTestId('nav-backdrop'));
    expect(screen.queryByRole('dialog', { name: 'Menü' })).not.toBeInTheDocument();
  });

  it('çekmeceden sayfa seçilince kapanır ve odak yeni içeriğe geçer', async () => {
    mockCompact(true);
    const { user } = renderApp({ user: AGENT, route: '/kayitlar' });
    await screen.findByRole('heading', { level: 1, name: 'Kayıtlar' });
    await user.click(screen.getByRole('button', { name: 'Menü' }));
    const drawer = screen.getByRole('dialog', { name: 'Menü' });
    await user.click(within(drawer).getByRole('link', { name: 'Yeni talep' }));
    await screen.findByRole('heading', { level: 1, name: /talep/i });
    expect(screen.queryByRole('dialog', { name: 'Menü' })).not.toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveFocus();
  });

  it('geniş ekranda kenar çubuğu her zaman görünür, çekmece/dialog yok', async () => {
    mockCompact(false);
    renderApp({ user: AGENT, route: '/kayitlar' });
    await screen.findByRole('heading', { level: 1, name: 'Kayıtlar' });
    expect(screen.getByRole('navigation', { name: 'Ana menü' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Menüyü kapat' })).not.toBeInTheDocument();
  });
});
