import { expect, test } from '@playwright/test';
import { API, bearer, closeAll, loginAs } from './helpers';

/**
 * Faz 2 yetki sınırı: son kullanıcı (requester) değişiklik ve CMDB ekranlarına ulaşamaz;
 * menüde görmez, doğrudan bağlantıda yetkisiz sayfası görür, API 403 döner.
 */
test('requester /degisiklikler ve /cmdb’ye erişemez (arayüz + API 403)', async ({
  browser,
  request,
}) => {
  const page = await loginAs(browser, 'son.kullanici', ['requester']);
  const nav = page.getByRole('navigation').first();
  await expect(nav.getByRole('link', { name: 'Değişiklikler' })).toHaveCount(0);
  await expect(nav.getByRole('link', { name: 'CMDB' })).toHaveCount(0);

  for (const path of ['/degisiklikler', '/degisiklikler/yeni', '/cmdb']) {
    await page.goto(path);
    await expect(
      page.getByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
    ).toBeVisible();
  }

  const asRequester = bearer('son.kullanici', 'requester');
  const calls: [string, string, unknown?][] = [
    ['GET', '/changes'],
    ['GET', '/changes/calendar?from=2030-01-01T00:00:00Z&to=2030-01-31T00:00:00Z'],
    ['GET', '/changes/templates'],
    ['POST', '/changes', { title: 'Yetkisiz', change_type: 'normal' }],
    ['GET', '/cmdb/cis'],
    ['POST', '/cmdb/cis', { name: 'yetkisiz', ci_class: 'server' }],
    ['POST', '/cmdb/impact', { ci_ids: ['yok'] }],
  ];
  for (const [method, path, data] of calls) {
    const response = await request.fetch(`${API}${path}`, { method, headers: asRequester, data });
    expect(response.status(), `${method} ${path}`).toBe(403);
  }
  // Yalnızca onaylayıcı (approver) da değişiklik/CMDB göremez
  const asApprover = bearer('sef.ali', 'approver');
  expect((await request.get(`${API}/changes`, { headers: asApprover })).status()).toBe(403);
  expect((await request.get(`${API}/cmdb/cis`, { headers: asApprover })).status()).toBe(403);

  await closeAll(page);
});
