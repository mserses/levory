import { expect, test, type APIRequestContext } from '@playwright/test';
import { FAZ3_API, bearer, closeAll, idFromUrl, loginAs } from './helpers';

/**
 * Faz 3 kapısı — uçtan uca: standart talep insansız karşılanır; R3 adım iki farklı insan onayı
 * olmadan çalışmaz. Backend: gölge mod kapalı, varsayılan güven eşiği, sahte Entra ID / M365 dizini
 * (demo: herkesin yöneticisi `zeynep`), standart grup izin listesi `sg-yazilim-standart, …`.
 */

interface LinkedAction {
  id: string;
  number: string;
  risk_class: string;
  state: string;
}
interface RequestSnapshot {
  number: string;
  state: string;
  touchless: boolean | null;
  touchless_reason: string | null;
  human_approvals: number;
  actions: LinkedAction[];
}

async function requestOf(request: APIRequestContext, id: string, user: string) {
  const response = await request.get(`${FAZ3_API}/requests/${id}`, {
    headers: bearer(user, 'requester'),
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as RequestSnapshot;
}

async function actionOf(request: APIRequestContext, id: string) {
  const response = await request.get(`${FAZ3_API}/actions/${id}`, {
    headers: bearer('ayse', 'agent,approver'),
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as { state: string; approvals: unknown[] };
}

test('standart yazılım grubu: katalogdan sipariş -> insansız karşılanır, rozet görünür', async ({
  browser,
  request,
}) => {
  const ali = await loginAs(browser, 'ali', ['requester']);
  await ali.goto('/katalog');
  await ali.getByRole('link', { name: 'Standart yazılım grubu erişimi' }).click();
  await ali.getByRole('textbox', { name: /^Yazılım grubu/ }).fill('sg-yazilim-standart');
  await ali.getByRole('button', { name: 'Talebi gönder' }).click();

  await expect(ali).toHaveURL(/\/taleplerim\/[^/?]+$/);
  await expect(ali.getByText('✓ İnsansız çözüldü')).toBeVisible();
  await expect(ali.getByText(/insansız çözüldü/).first()).toBeVisible();
  const steps = ali.getByRole('list', { name: 'Karşılama adımları' }).getByRole('listitem');
  await expect(steps.first()).toContainText('Otomasyon (ai:fulfilment)');

  // Sunucu: karşılandı, insansız, hiçbir eylem isteği açılmadı
  const snap = await requestOf(request, idFromUrl(ali), 'ali');
  expect(snap.state).toBe('fulfilled');
  expect(snap.touchless).toBe(true);
  expect(snap.actions).toEqual([]);
  await closeAll(ali);
});

test('yetkili grup: iş onayı -> R3 eylem isteği 1/2 bekler -> ikinci onayla yürür -> insansız değil', async ({
  browser,
  request,
}) => {
  // 1) Talep eden siparişi verir: yönetici onayı bekler
  const mehmet = await loginAs(browser, 'mehmet', ['requester']);
  await mehmet.goto('/katalog');
  await mehmet.getByRole('link', { name: 'Yetkili grup erişimi' }).click();
  await mehmet.getByRole('textbox', { name: /^Yetkili grup/ }).fill('sg-domain-admins');
  await mehmet
    .getByRole('textbox', { name: /^Gerekçe/ })
    .fill('Sunucu bakımı için geçici etki alanı yetkisi');
  await mehmet.getByRole('button', { name: 'Talebi gönder' }).click();
  await expect(mehmet).toHaveURL(/\/taleplerim\/[^/?]+$/);
  const requestId = idFromUrl(mehmet);
  await expect(mehmet.getByText(/iş onayı bekliyor/)).toBeVisible();
  // Talep eden kendi talebine iş onayı veremez
  await expect(
    mehmet.getByRole('region', { name: 'İş onayı' }).getByRole('button', { name: 'Onayla' }),
  ).toHaveCount(0);
  const { number } = await requestOf(request, requestId, 'mehmet');

  // 2) Yönetici (dizinde zeynep) "Onayımı bekleyenler" sekmesinden onaylar
  const zeynep = await loginAs(browser, 'zeynep', ['requester']);
  await zeynep.goto('/taleplerim?sekme=onay');
  const row = zeynep.getByRole('row').filter({ hasText: number });
  await row.getByRole('link', { name: 'Yetkili grup erişimi' }).click();
  await expect(zeynep.getByText(/sizin iş onayınızı bekliyor/)).toBeVisible();
  const decision = zeynep.getByRole('region', { name: 'İş onayı' });
  await decision.getByLabel(/Yorum/).fill('Bakım penceresi için uygun');
  await decision.getByRole('button', { name: 'Onayla' }).click();
  await expect(zeynep.getByText(/Bir adım BT onayı bekliyor/)).toBeVisible();

  // Gruba ekleme R3: eylem isteği açıldı, dış sisteme yazılmadı
  let snap = await requestOf(request, requestId, 'mehmet');
  expect(snap.state).toBe('fulfilling');
  expect(snap.actions).toHaveLength(1);
  const action = snap.actions[0] as LinkedAction;
  expect(action.risk_class).toBe('R3');
  expect(action.state).toBe('pending');

  // 3) Birinci BT onaylayan: kayıt edilir ama yürütülmez (1/2)
  const first = await loginAs(browser, 'sef.ali', ['approver']);
  await first.goto(`/onaylar?sekme=eylem&istek=${action.id}`);
  let card = first.locator(`[data-action-id="${action.id}"]`);
  await expect(card).toContainText('R3: 0/2 onay');
  await card.getByRole('button', { name: 'Onayla' }).click();
  await first
    .getByRole('alertdialog', { name: `${action.number} onaylansın mı?` })
    .getByRole('button', { name: 'Onayımı kaydet' })
    .click();
  await expect(card).toContainText('R3: 1/2 onay');
  expect((await actionOf(request, action.id)).state).toBe('pending');
  snap = await requestOf(request, requestId, 'mehmet');
  expect(snap.state).toBe('fulfilling');

  // 4) İkinci, farklı BT onaylayan: onaylar ve eylem onaylayanın kimliğiyle yürür
  const second = await loginAs(browser, 'sef.veli', ['approver']);
  await second.goto(`/onaylar?sekme=eylem&istek=${action.id}`);
  card = second.locator(`[data-action-id="${action.id}"]`);
  await expect(card).toContainText('R3: 1/2 onay');
  await card.getByRole('button', { name: 'Onayla' }).click();
  await second
    .getByRole('alertdialog', { name: `${action.number} onaylansın mı?` })
    .getByRole('button', { name: 'Onayla ve yürüt' })
    .click();
  await expect.poll(async () => (await actionOf(request, action.id)).state).toBe('executed');

  // 5) Talep karşılandı ama insansız değil: kim katkı verdi görünür
  snap = await requestOf(request, requestId, 'mehmet');
  expect(snap.state).toBe('fulfilled');
  expect(snap.touchless).toBe(false);
  expect(snap.touchless_reason).toContain('zeynep');
  expect(snap.touchless_reason).toContain('sef.ali');
  expect(snap.touchless_reason).toContain('sef.veli');
  await mehmet.reload();
  await expect(mehmet.getByText('İnsan katkısıyla çözüldü')).toBeVisible();
  await expect(mehmet.getByText('✓ İnsansız çözüldü')).toHaveCount(0);
  await closeAll(mehmet, zeynep, first, second);
});
