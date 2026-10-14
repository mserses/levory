import { expect, test, type Page } from '@playwright/test';
import { FAZ4_API, bearer, closeAll, faz4TimeTravel, loginAs } from './helpers';

/**
 * Faz 4 sürekli iyileştirme — uçtan uca: tohumlanmış SENTETİK MTTR gerilemesi (`network_ops`: önceki
 * 7 gün ~4 sa, son 7 gün ~10 sa) -> CSI ajanı (`ai:csi`, `POST /improvements/review` = zamanlayıcı
 * işi `csi_review`) MTTR'ye bağlı iyileştirme fikri önerir (taban 10 sa, hedef 4 sa) -> onaylayan
 * onaylar -> destek uzmanı başlatır ve uygulandı işaretler -> pencere dolmadan ölçüm reddedilir ->
 * senaryo saati ölçüm penceresi kadar ileri sarılır (`faz4_data.py time-travel`; uygulama sonrası
 * olaylar ~3 sa) -> ölçülür -> onaylayan kapatır -> ölçülen değer ve hedef sonucu görünür.
 */

interface Improvement {
  id: string;
  number: string;
  title: string;
  state: string;
  detected_by: string;
  signal_key: string | null;
  baseline_value: number | null;
  target_value: number | null;
  measured_value: number | null;
  target_met: boolean | null;
  measurement: { delta_pct?: number | null } | null;
}

const AGENT = bearer('ayse', 'agent');

async function step(page: Page, label: string): Promise<void> {
  await page
    .getByRole('group', { name: 'Durum adımları' })
    .getByRole('button', { name: label })
    .click();
  const dialog = page.getByRole('dialog', { name: label });
  await dialog.getByRole('button', { name: 'Kaydet' }).click();
  await expect(dialog).toBeHidden();
}

test('MTTR gerilemesi -> CSI önerisi -> onay -> uygulama -> ölçüm -> kapanış, ölçülen değer görünür', async ({
  browser,
  request,
}) => {
  // 1) CSI ajanı sinyali bulur ve fikir önerir (R1, gölge mod kapalı)
  const review = await request.post(`${FAZ4_API}/improvements/review`, { headers: AGENT });
  expect(review.status(), await review.text()).toBe(200);
  expect(((await review.json()) as { proposed: number }).proposed).toBeGreaterThanOrEqual(1);
  const list = await request.get(`${FAZ4_API}/improvements`, { headers: AGENT });
  const proposed = ((await list.json()) as Improvement[]).find(
    (i) => i.signal_key === 'mttr_increase:assignment_group:network_ops',
  );
  expect(proposed, 'network_ops MTTR önerisi').toBeDefined();
  const imp = proposed as Improvement;
  expect(imp.state).toBe('idea');
  expect(imp.detected_by).toBe('ai:csi');
  expect(imp.baseline_value).toBeCloseTo(10, 1);
  expect(imp.target_value).toBeCloseTo(4, 1);
  // Aynı sinyal ikinci incelemede yeniden önerilmez
  const again = await request.post(`${FAZ4_API}/improvements/review`, { headers: AGENT });
  expect(((await again.json()) as { proposed: number }).proposed).toBe(0);

  // 2) Onaylayan kayıttan açar, ölçüt ve tabanı görür, onaylar
  const approver = await loginAs(browser, 'sef.ali', ['approver']);
  await approver.goto('/iyilestirmeler');
  await approver.getByRole('link', { name: imp.title }).click();
  await expect(approver).toHaveURL(new RegExp(`/iyilestirmeler/${imp.id}$`));
  await expect(approver.getByText('Yapay zekâ önerisi')).toBeVisible();
  const measureCard = approver.getByRole('region', { name: 'Ölçüm' });
  await expect(measureCard).toContainText('10 sa');
  await expect(measureCard).toContainText('4 sa');
  await expect(measureCard.getByText('Henüz ölçülmedi')).toBeVisible();
  await step(approver, 'Onayla');
  await expect(approver.getByText('Onaylandı').first()).toBeVisible();

  // 3) Destek uzmanı başlatır ve uygulandı işaretler; pencere dolmadan ölçüm reddedilir
  const agent = await loginAs(browser, 'ayse', ['agent']);
  await agent.goto(`/iyilestirmeler/${imp.id}`);
  await step(agent, 'Uygulamaya başla');
  await step(agent, 'Uygulandı olarak işaretle');
  await agent
    .getByRole('group', { name: 'Durum adımları' })
    .getByRole('button', { name: 'Ölç' })
    .click();
  const early = agent.getByRole('dialog', { name: 'Ölç' });
  await early.getByRole('button', { name: 'Kaydet' }).click();
  await expect(early.getByRole('alert')).toContainText('henüz dolmadı');
  await early.getByRole('button', { name: 'Vazgeç' }).click();

  // 4) Ölçüm penceresi (7 gün) geçer: senaryo saati 8 gün ileri
  faz4TimeTravel(imp.id, 8);
  await agent.reload();
  await step(agent, 'Ölç');
  const agentCard = agent.getByRole('region', { name: 'Ölçüm' });
  await expect(agentCard).toContainText('3 sa');
  await expect(agentCard.getByText('Hedef tuttu')).toBeVisible();

  // 5) Onaylayan kapatır; ölçülen değer ve sonuç kalıcı
  await approver.reload();
  await step(approver, 'Kapat');
  await expect(approver.getByText('Kapandı').first()).toBeVisible();
  await expect(measureCard).toContainText('3 sa');
  await expect(measureCard.getByText('Hedef tuttu')).toBeVisible();
  await expect(approver.getByRole('group', { name: 'Durum adımları' })).toHaveCount(0);

  const final = await request.get(`${FAZ4_API}/improvements/${imp.id}`, { headers: AGENT });
  const closed = (await final.json()) as Improvement;
  expect(closed.state).toBe('closed');
  expect(closed.measured_value).toBeCloseTo(3, 1);
  expect(closed.target_met).toBe(true);
  expect(closed.measurement?.delta_pct ?? 0).toBeLessThan(-50);
  await closeAll(agent, approver);
});
