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
 * Faz 2 standart değişiklik (gerçek backend): düşük riskli CI'da şablondan açılan standart
 * değişiklik CAB'ı atlar; aynı şablon kritiklik-1 prod CI'da kullanılınca değerlendirmede
 * normal'e çevrilir ve CAB'sız zamanlanamaz (arayüz + API).
 */
interface Created {
  id: string;
  number: string;
}

const MGR = 'standart.mudur';
const AGENT = 'standart.uzman';
const asMgr = bearer(MGR, 'change_manager');
const asAgent = bearer(AGENT, 'agent');

async function newStandardChange(page: Page, template: string, ciName: string, title: string) {
  const start = new Date(Date.now() + 2 * 24 * 3600_000);
  await page.goto('/degisiklikler/yeni');
  await page.getByLabel('Standart (şablondan)').check();
  await page.getByLabel('Standart şablon').selectOption({ label: template });
  // Boş planlar şablondan dolar
  await expect(page.getByLabel('Geri dönüş planı')).toHaveValue('Önceki sertifikaya geri dön.');
  await page.getByLabel('Başlık').fill(title);
  await page.getByLabel('CI ara').fill(ciName);
  await page.getByRole('button', { name: `${ciName} ekle` }).click();
  await page.getByLabel('Planlanan başlangıç').fill(localInput(start));
  await page.getByLabel('Planlanan bitiş').fill(localInput(new Date(start.getTime() + 3600_000)));
  await page.getByRole('button', { name: 'Taslağı kaydet' }).click();
  await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
  return idFromUrl(page);
}

test('standart şablon: düşük risk CAB atlar, kritiklik-1 prod CI normal’e çevrilir', async ({
  browser,
  request,
}) => {
  test.setTimeout(90_000);
  const tag = unique('e2e').replace(' ', '-');
  const templateName = unique('Sertifika yenileme');
  await apiPost(request, '/changes/templates', asMgr, {
    name: templateName,
    description: 'TLS sertifikası yenileme',
    max_risk: 'low',
    allowed_ci_classes: ['server'],
    implementation_plan: 'Yeni sertifikayı yükle ve servisi yeniden yükle.',
    backout_plan: 'Önceki sertifikaya geri dön.',
    test_plan: 'TLS el sıkışmasını doğrula.',
  });
  const lowCi = await apiPost<Created>(request, '/cmdb/cis', asMgr, {
    name: `test-sunucu-${tag}`,
    ci_class: 'server',
    environment: 'dev',
    criticality: 4,
  });
  const critCi = await apiPost<Created>(request, '/cmdb/cis', asMgr, {
    name: `cekirdek-sunucu-${tag}`,
    ci_class: 'server',
    environment: 'prod',
    criticality: 1,
  });
  expect(lowCi.id && critCi.id).toBeTruthy();

  const agent = await loginAs(browser, AGENT, ['agent']);

  // 1) Düşük risk: standart kalır, CAB adımı yok, talep eden doğrudan zamanlar
  const lowId = await newStandardChange(
    agent,
    templateName,
    `test-sunucu-${tag}`,
    unique('Sertifika (test)'),
  );
  await expect(agent.getByText(/Standart değişiklik önceden onaylıdır/)).toBeVisible();
  await agent.getByRole('button', { name: 'Değerlendirmeye gönder' }).click();
  await expect.poll(async () => (await changeOf(request, lowId, asAgent)).state).toBe('assess');
  await agent.reload();
  await expect(agent.getByRole('button', { name: 'CAB onayına gönder' })).toHaveCount(0);
  await agent.getByRole('button', { name: 'Zamanla' }).click();
  await expect.poll(async () => (await changeOf(request, lowId, asAgent)).state).toBe('scheduled');
  const low = await changeOf(request, lowId, asAgent);
  expect(low).toMatchObject({ change_type: 'standard', risk_level: 'low' });
  const lowApprovals = await request.get(`${API}/changes/${lowId}/approvals`, { headers: asMgr });
  expect(((await lowApprovals.json()) as { items: unknown[] }).items).toHaveLength(0);

  // 2) Aynı şablon, kritiklik-1 prod CI: değerlendirmede normal'e çevrilir, CAB zorunlu
  const critId = await newStandardChange(
    agent,
    templateName,
    `cekirdek-sunucu-${tag}`,
    unique('Sertifika (prod)'),
  );
  await agent.getByRole('button', { name: 'Değerlendirmeye gönder' }).click();
  await expect.poll(async () => (await changeOf(request, critId, asAgent)).state).toBe('assess');
  const crit = await changeOf(request, critId, asAgent);
  expect(crit.change_type).toBe('normal');
  expect(crit.risk_level).not.toBe('low');
  await agent.reload();
  await expect(agent.getByText('Normal', { exact: true }).first()).toBeVisible();
  // Uzman (talep eden) zamanlayamaz ve CAB'a da yalnızca yönetici gönderir
  await expect(agent.getByRole('button', { name: 'Zamanla' })).toHaveCount(0);
  const bypass = await request.post(`${API}/changes/${critId}/transition`, {
    headers: asAgent,
    data: { to_state: 'scheduled' },
  });
  expect(bypass.status()).toBe(422);
  const bypassMgr = await request.post(`${API}/changes/${critId}/transition`, {
    headers: asMgr,
    data: { to_state: 'scheduled' },
  });
  expect(bypassMgr.status()).toBe(422);
  expect((await changeOf(request, critId, asMgr)).state).toBe('assess');

  // Değişiklik yöneticisi yalnızca CAB yolunu görür
  const mgr = await loginAs(browser, MGR, ['change_manager']);
  await mgr.goto(`/degisiklikler/${critId}`);
  await expect(mgr.getByRole('button', { name: 'CAB onayına gönder' })).toBeVisible();
  await expect(mgr.getByRole('button', { name: 'Zamanla' })).toHaveCount(0);

  await closeAll(agent, mgr);
});
