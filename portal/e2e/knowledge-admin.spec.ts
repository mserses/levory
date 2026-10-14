import { expect, test } from '@playwright/test';
import { closeAll, loginAs } from './helpers';

test.describe.configure({ mode: 'serial' });

test('bilgi bankası: makale oluştur + yayınla + ara', async ({ browser }) => {
  const token = `Zebratünel${Date.now().toString(36)}`;
  const title = `${token} VPN sertifika hatası çözümü`;
  const agent = await loginAs(browser, 'ayse', ['agent']);

  await agent.goto('/bilgi-bankasi');
  await expect(agent.getByRole('heading', { level: 1, name: 'Bilgi bankası' })).toBeVisible();
  await agent.getByRole('button', { name: 'Yeni makale' }).click();
  const dialog = agent.getByRole('dialog', { name: 'Yeni makale' });
  await dialog.getByLabel('Başlık').fill(title);
  await dialog.getByLabel('Kategori').selectOption({ label: 'Ağ ve bağlantı' });
  await dialog
    .getByLabel('İçerik')
    .fill('1. VPN istemcisini kapatın.\n2. Sertifikayı yenileyin.\n3. Yeniden bağlanın.');
  await dialog.getByRole('button', { name: 'Kaydet ve yayınla' }).click();

  await expect(agent.getByText('Makale yayınlandı')).toBeVisible();
  await expect(agent.getByRole('heading', { level: 1, name: title })).toBeVisible();
  await expect(agent.getByText('Yayında').first()).toBeVisible();

  await agent.goto('/bilgi-bankasi');
  await agent.getByLabel('Makalelerde ara').fill(token);
  await agent.getByRole('button', { name: 'Ara', exact: true }).click();
  await expect(agent).toHaveURL(/q=/);
  const result = agent.getByRole('link', { name: new RegExp(token) }).first();
  await expect(result).toBeVisible();
  // Eşleşen sözcük <mark> ile vurgulanır (sunucu HTML'i yorumlanmaz)
  await expect(
    agent
      .locator('mark')
      .filter({ hasText: new RegExp(token, 'i') })
      .first(),
  ).toBeVisible();
  await result.click();
  await expect(agent.getByRole('heading', { level: 1, name: title })).toBeVisible();
  await closeAll(agent);
});

test('yönetici denetim zincirini doğrular', async ({ browser }) => {
  const admin = await loginAs(browser, 'yonetici', ['admin']);
  await admin.goto('/yonetim/denetim');
  await expect(admin.getByRole('heading', { level: 1 })).toBeVisible();
  // Önceki senaryoların bıraktığı kayıtlar listede
  await expect(admin.getByRole('table').first()).toBeVisible();
  await expect(admin.getByRole('table').first()).toContainText('Makale oluşturuldu');
  await admin.getByRole('button', { name: 'Zinciri doğrula' }).click();
  await expect(admin.getByTestId('verify-status')).toContainText('Zincir geçerli');
  await closeAll(admin);
});
