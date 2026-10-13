import { expect, test, type Page } from '@playwright/test';
import {
  API,
  apiPost,
  bearer,
  changeOf,
  closeAll,
  idFromUrl,
  localInput,
  loginAs,
  unique,
} from './helpers';

/**
 * Faz 2 normal değişiklik (gerçek backend, sahte LLM): change_manager bağımlısı olan bir CMDB
 * CI'ı için RFC açar → riski değerlendirir → gönderir → CAB'a yollar; talep eden oy veremez, iki
 * CAB üyesi onaylar → zamanlanır; aynı CI'da çakışan ikinci değişiklik zamanlanamaz → uygulanır →
 * gözden geçirilir → kapatılır.
 */
interface Created {
  id: string;
  number: string;
}

const MGR = 'degisim.mudur';
const MGR2 = 'degisim.mudur2';
const asMgr = bearer(MGR, 'change_manager');

async function expectState(
  page: Page,
  request: Parameters<typeof changeOf>[0],
  id: string,
  state: string,
) {
  await expect.poll(async () => (await changeOf(request, id, asMgr)).state).toBe(state);
  await page.reload();
}

test('normal RFC: risk → CAB (iki oy, talep eden hariç) → zamanla → çakışma engeli → uygula → kapat', async ({
  browser,
  request,
}) => {
  test.setTimeout(120_000);
  const tag = unique('e2e').replace(' ', '-');
  // CMDB: ödeme uygulaması veritabanına bağımlı (veritabanı değişirse uygulama etkilenir)
  const dbCi = await apiPost<Created>(request, '/cmdb/cis', asMgr, {
    name: `odeme-db-${tag}`,
    ci_class: 'database',
    environment: 'prod',
    criticality: 2,
  });
  const appCi = await apiPost<Created>(request, '/cmdb/cis', asMgr, {
    name: `odeme-uygulama-${tag}`,
    ci_class: 'application',
    environment: 'prod',
    criticality: 2,
  });
  await apiPost(request, '/cmdb/relationships', asMgr, {
    source_id: appCi.id,
    target_id: dbCi.id,
    type: 'depends_on',
  });

  // Pencere şimdi içinde: uygulama yalnızca planlanan pencere içinde başlatılabilir
  const start = new Date(Date.now() - 10 * 60_000);
  const end = new Date(Date.now() + 3 * 3600_000);
  const title = unique('Ödeme veritabanı yaması');

  const mgr = await loginAs(browser, MGR, ['change_manager']);
  await mgr.goto('/degisiklikler/yeni');
  await expect(
    mgr.getByRole('heading', { level: 1, name: 'Yeni değişiklik talebi' }),
  ).toBeVisible();
  await mgr.getByLabel('Başlık').fill(title);
  await mgr.getByLabel('CI ara').fill(`odeme-db-${tag}`);
  await mgr.getByRole('button', { name: `odeme-db-${tag} ekle` }).click();
  // Canlı etki önizlemesi bağımlı uygulamayı gösterir
  const preview = mgr.getByRole('region', { name: 'Etki önizlemesi' });
  await expect(preview.getByRole('link', { name: `odeme-uygulama-${tag}` })).toBeVisible();
  await mgr
    .getByLabel('Uygulama planı')
    .fill('Yamayı bakım penceresinde uygula, servisleri yeniden başlat.');
  await mgr.getByLabel('Geri dönüş planı').fill('Anlık görüntüden geri dön ve uygulamayı doğrula.');
  await mgr.getByLabel('Test planı').fill('Ödeme duman testi.');
  await mgr.getByLabel('Planlanan başlangıç').fill(localInput(start));
  await mgr.getByLabel('Planlanan bitiş').fill(localInput(end));
  await mgr.getByRole('button', { name: 'Taslağı kaydet' }).click();
  await expect(mgr).toHaveURL(/\/degisiklikler\/[^/]+$/);
  await expect(mgr.getByRole('heading', { level: 1, name: title })).toBeVisible();
  const id = idFromUrl(mgr);
  const first = await changeOf(request, id, asMgr);
  expect(first.state).toBe('draft');

  // Yapay zekâ risk değerlendirmesi (sahte LLM anlatısı); durum değişmez
  await mgr.getByRole('button', { name: 'Riski değerlendir' }).click();
  const decision = mgr.getByRole('article', { name: 'Yapay zekâ risk kararı' });
  await expect(decision.getByText(/\[fake\] Deterministik risk skoru/)).toBeVisible();
  expect((await changeOf(request, id, asMgr)).state).toBe('draft');

  await mgr.getByRole('button', { name: 'Değerlendirmeye gönder' }).click();
  await expect(mgr.getByRole('button', { name: 'CAB onayına gönder' })).toBeVisible();
  await mgr.getByRole('button', { name: 'CAB onayına gönder' }).click();
  await expectState(mgr, request, id, 'authorize');

  // Talep eden oy veremez: arayüzde form kapalı, API 403
  const vote = mgr.getByRole('form', { name: 'CAB oyu' });
  await expect(vote.getByText(/Talep eden olduğunuz değişiklik/)).toBeVisible();
  await expect(vote.getByRole('button', { name: 'Oyu gönder' })).toBeDisabled();
  const selfVote = await request.post(`${API}/changes/${id}/approvals`, {
    headers: asMgr,
    data: { decision: 'approve' },
  });
  expect(selfVote.status()).toBe(403);
  // Yetersiz onayla zamanlama reddedilir
  const early = await request.post(`${API}/changes/${id}/transition`, {
    headers: asMgr,
    data: { to_state: 'scheduled' },
  });
  expect(early.status()).toBe(422);
  expect((await changeOf(request, id, asMgr)).state).toBe('authorize');

  // İki CAB üyesi arayüzden onaylar
  const cabPages: Page[] = [];
  for (const member of ['kurul.ayse', 'kurul.mert']) {
    const cab = await loginAs(browser, member, ['cab']);
    cabPages.push(cab);
    await cab.goto(`/degisiklikler/${id}`);
    const form = cab.getByRole('form', { name: 'CAB oyu' });
    await form.getByLabel('Onayla').check();
    await form.getByLabel('Yorum').fill('Geri dönüş planı yeterli.');
    await form.getByRole('button', { name: 'Oyu gönder' }).click();
    await expect(cab.getByText('Onayınız kaydedildi')).toBeVisible();
    // Oy verdikten sonra form kapanır (ikinci oy yok)
    await expect(form.getByRole('button', { name: 'Oyu gönder' })).toBeDisabled();
  }
  // CAB üyesi zamanlayamaz (yalnızca change_manager)
  for (const cab of cabPages) {
    await expect(cab.getByRole('button', { name: 'Zamanla' })).toHaveCount(0);
  }

  await mgr.reload();
  await expect(mgr.getByText('Onay 2/2')).toBeVisible();
  await mgr.getByRole('button', { name: 'Zamanla' }).click();
  await expectState(mgr, request, id, 'scheduled');

  // Aynı CI'da çakışan ikinci değişiklik (başka bir yönetici, CAB onaylı) zamanlanamaz
  const asMgr2 = bearer(MGR2, 'change_manager');
  const second = await apiPost<Created>(request, '/changes', asMgr2, {
    title: unique('Çakışan veritabanı bakımı'),
    change_type: 'normal',
    implementation_plan: 'Dizin yeniden oluşturma.',
    backout_plan: 'Eski dizinlere dön ve doğrula.',
    test_plan: 'Sorgu süresi ölçümü.',
    planned_start: new Date(Date.now() + 30 * 60_000).toISOString(),
    planned_end: new Date(Date.now() + 2 * 3600_000).toISOString(),
    affected_ci_ids: [dbCi.id],
  });
  for (const to of ['assess', 'authorize']) {
    await apiPost(request, `/changes/${second.id}/transition`, asMgr2, { to_state: to }, 200);
  }
  for (const member of ['kurul.ayse', 'kurul.mert']) {
    await apiPost(request, `/changes/${second.id}/approvals`, bearer(member, 'cab'), {
      decision: 'approve',
    });
  }
  await mgr.goto(`/degisiklikler/${second.id}`);
  await expect(mgr.getByText('Zamanlama çakışmaları')).toBeVisible();
  await mgr.getByRole('button', { name: 'Zamanla' }).click();
  const dialog = mgr.getByRole('alertdialog', { name: 'Planlanan pencerede çakışma var' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(first.number)).toBeVisible();
  await dialog.getByRole('button', { name: 'Tamam' }).click();
  expect((await changeOf(request, second.id, asMgr)).state).toBe('authorize');

  // İlk değişiklik: uygula → başarıyla tamamlandı → PIR ile kapat
  await mgr.goto(`/degisiklikler/${id}`);
  await mgr.getByRole('button', { name: 'Uygulamayı başlat' }).click();
  await expectState(mgr, request, id, 'implement');
  await mgr.getByRole('button', { name: 'Başarıyla tamamlandı' }).click();
  await expectState(mgr, request, id, 'review');
  await mgr.getByLabel('PIR notu').fill('Yama sorunsuz uygulandı; sapma yok.');
  await mgr.getByRole('button', { name: 'Gözden geçirmeyi kaydet ve kapat' }).click();
  await expectState(mgr, request, id, 'closed');
  await expect(mgr.getByText('Yama sorunsuz uygulandı; sapma yok.')).toBeVisible();

  await closeAll(mgr, ...cabPages);
});
