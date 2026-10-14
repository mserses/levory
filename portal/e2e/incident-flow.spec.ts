import { expect, test, type Page } from '@playwright/test';
import { closeAll, idFromUrl, loginAs, unique } from './helpers';

/**
 * Ana akış: son kullanıcı talep açar → destek uzmanı yapay zekâ ile sınıflandırır (P1 önerisi,
 * onay gerekli) → onaylayıcı onaylar → uzman güncel önceliği görür. Ardından bayat öneri: kayıt
 * öneriden sonra değişirse onay 409 "yeniden triage gerekli" ile reddedilir.
 */
test.describe.configure({ mode: 'serial' });

const DESCRIPTION = 'Tüm şirket VPN bağlantısı kuramıyor, iş durdu. Sabahtan beri hata alıyoruz.';

async function openRequest(page: Page, title: string): Promise<string> {
  await page.goto('/talep');
  await expect(page.getByRole('heading', { level: 1, name: 'Yeni talep' })).toBeVisible();
  await page.getByLabel('Başlık').fill(title);
  await page.getByLabel('Açıklama').fill(DESCRIPTION);
  await page.getByRole('button', { name: 'Talebi gönder' }).click();
  const number = page.getByText(/INC\d{4,}/).first();
  await expect(number).toBeVisible();
  const match = /INC\d+/.exec((await number.textContent()) ?? '');
  if (!match) throw new Error('Kayıt numarası bulunamadı');
  return match[0];
}

/** Uzman kuyruktan kaydı açar ve yapay zekâ sınıflandırmasını çalıştırır; kayıt kimliğini döner */
async function triageFromQueue(page: Page, title: string): Promise<string> {
  await page.goto('/kayitlar?sirala=yeni');
  await page.getByRole('link', { name: title }).click();
  await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
  await page.getByRole('button', { name: 'Yapay zekâ ile sınıflandır' }).click();
  await expect(page.getByText('Yapay zekâ kararı: Onay bekliyor')).toBeVisible();
  return idFromUrl(page);
}

function decisionCard(page: Page, title: string) {
  return page.getByTestId('decision-item').filter({ hasText: title });
}

const detailHeader = (page: Page) =>
  page.locator('header').filter({ has: page.getByRole('heading', { level: 1 }) });

test('talep → triage (P1, onay gerekli) → onay → uzman güncel önceliği görür', async ({
  browser,
}) => {
  const title = unique('VPN çöktü');

  // 1) Son kullanıcı talep açar ve Taleplerim'de görür
  const requester = await loginAs(browser, 'zeynep', ['requester']);
  const number = await openRequest(requester, title);
  await requester
    .getByRole('link', { name: /Taleplerim/ })
    .first()
    .click();
  await expect(requester.getByRole('heading', { level: 1, name: 'Taleplerim' })).toBeVisible();
  const row = requester.getByRole('row').filter({ hasText: number });
  await expect(row).toContainText(title);

  // 2) Destek uzmanı sınıflandırır: P1'e yükseltme önerisi otomatik uygulanmaz
  const agent = await loginAs(browser, 'ayse', ['agent']);
  await triageFromQueue(agent, title);
  await expect(detailHeader(agent)).not.toContainText('P1');
  const decisions = agent.getByRole('region', { name: 'Yapay zekâ kararları' });
  await expect(decisions.getByText('Onay bekliyor').first()).toBeVisible();

  // 3) Onaylayıcı öneriyi onaylar
  const approver = await loginAs(browser, 'sef.ali', ['approver']);
  await approver.goto('/onaylar');
  const card = decisionCard(approver, title);
  await expect(card).toBeVisible();
  await expect(card).toContainText('P1');
  await card.getByRole('button', { name: 'Onayla ve uygula' }).click();
  const dialog = approver.getByRole('alertdialog', { name: 'Öneri onaylansın mı?' });
  await expect(dialog).toContainText('P1');
  await dialog.getByRole('button', { name: 'Onayla ve uygula' }).click();
  await expect(approver.getByText('Öneri onaylandı')).toBeVisible();
  await expect(decisionCard(approver, title)).toHaveCount(0);

  // 4) Uzman güncel önceliği görür
  await agent.reload();
  await expect(detailHeader(agent)).toContainText('P1 · Kritik');

  // Son kullanıcı yapay zekâ ayrıntısı görmez, önceliği görür
  await requester.reload();
  await expect(requester.getByRole('row').filter({ hasText: number })).toContainText('P1');

  await closeAll(requester, agent, approver);
});

test('bayat öneri onaylanınca 409 "yeniden triage gerekli" gösterilir', async ({ browser }) => {
  const title = unique('VPN kesintisi');
  const requester = await loginAs(browser, 'mehmet', ['requester']);
  await openRequest(requester, title);

  const agent = await loginAs(browser, 'ayse', ['agent']);
  await triageFromQueue(agent, title);

  // Onaylayıcı listeyi açmışken uzman kaydı değiştirir (sürüm artar)
  const approver = await loginAs(browser, 'sef.ali', ['approver']);
  await approver.goto('/onaylar');
  const card = decisionCard(approver, title);
  await expect(card).toBeVisible();

  await agent.getByRole('button', { name: 'Düzenle' }).click();
  await agent.getByLabel('Atanan').fill('ayse');
  await agent.getByRole('button', { name: 'Kaydet' }).click();
  await expect(agent.getByText('Kayıt güncellendi')).toBeVisible();

  await card.getByRole('button', { name: 'Onayla ve uygula' }).click();
  await approver
    .getByRole('alertdialog', { name: 'Öneri onaylansın mı?' })
    .getByRole('button', { name: 'Onayla ve uygula' })
    .click();
  const alert = card.getByRole('alert');
  await expect(alert).toContainText('yeniden triage gerekli');
  await expect(card.getByRole('button', { name: 'Onayla ve uygula' })).toBeDisabled();

  // Öneri uygulanmadı: öncelik değişmedi
  await agent.reload();
  await expect(detailHeader(agent)).not.toContainText('P1');

  await closeAll(requester, agent, approver);
});
