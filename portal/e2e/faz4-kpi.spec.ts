import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { FAZ4_API, bearer, closeAll, loginAs } from './helpers';

/**
 * Faz 4 KPI panosu — tohumlanmış SENTETİK geçmişle (`e2e/faz4_data.py seed`) `/panolar` açılır:
 * KPI kutucukları `GET /kpi` ile aynı değeri (önceki dönem + örneklem) gösterir, dört grafik veri
 * tablosuyla çizilir, dönem seçici raporu yeniler. Talep eden panoyu göremez.
 */

interface KpiValue {
  key: string;
  name: string;
  value: number | null;
  count: number;
  previous: number | null;
  previous_count: number;
}

const hours = (v: number | null) =>
  v === null ? '—' : `${new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 1 }).format(v)} sa`;

async function apiKpis(request: APIRequestContext, days: number) {
  const response = await request.get(`${FAZ4_API}/kpi?days=${String(days)}&kpi=mttr&kpi=backlog`, {
    headers: bearer('ayse', 'agent'),
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return byKey((await response.json()) as { kpis: KpiValue[] });
}

function byKey(body: { kpis: KpiValue[] }): { mttr: KpiValue; backlog: KpiValue } {
  const find = (key: string) => {
    const kpi = body.kpis.find((k) => k.key === key);
    if (!kpi) throw new Error(`KPI yanıtta yok: ${key}`);
    return kpi;
  };
  return { mttr: find('mttr'), backlog: find('backlog') };
}

/** Panonun ana rapor isteği (bitiş saat başına sabitlenir): kutucuklar tam bu yanıtı göstermeli */
const mainReport = (page: Page, days: number) =>
  page.waitForResponse(
    (r) =>
      r.url().startsWith(`${FAZ4_API}/kpi?`) &&
      r.url().includes(`days=${String(days)}&`) &&
      r.url().includes('kpi=backlog'),
  );

test('KPI panosu tohumlanmış veriyle çizilir ve API ile aynı değerleri gösterir', async ({
  browser,
  request,
}) => {
  // Tohum: iki dönemde de çözülmüş olay ve açık iş yükü var
  const seeded = await apiKpis(request, 30);
  expect(seeded.mttr.count).toBeGreaterThan(0);
  expect(seeded.mttr.previous_count).toBeGreaterThan(0);
  expect(seeded.backlog.value).toBeGreaterThan(0);

  const agent = await loginAs(browser, 'ayse', ['agent']);
  const loaded = mainReport(agent, 30);
  await agent.goto('/panolar');
  const api = byKey((await (await loaded).json()) as { kpis: KpiValue[] });
  await expect(agent.getByRole('heading', { level: 1, name: 'Panolar' })).toBeVisible();

  const tile = agent.getByRole('article', { name: api.mttr.name });
  await expect(tile).toContainText(hours(api.mttr.value));
  await expect(tile).toContainText(`Örneklem: ${String(api.mttr.count)} kayıt`);
  await expect(tile).toContainText(
    `önceki dönem ${hours(api.mttr.previous)} (${String(api.mttr.previous_count)} kayıt)`,
  );
  const backlog = agent.getByRole('article', { name: api.backlog.name });
  await expect(backlog).toContainText(`Örneklem: ${String(api.backlog.count)} kayıt`);

  // Dört grafik, her biri erişilebilir özet + veri tablosu
  const charts = agent.getByRole('region', { name: 'Zaman içinde' });
  for (const title of [
    'MTTR — önceliğe göre',
    'SLA uyumu',
    'Birikim yaşlanması',
    'Otomasyon, yapay zekâ kabulü, değişiklik başarısı',
  ]) {
    const figure = agent.getByRole('figure', { name: title });
    await expect(figure).toBeVisible();
    await expect(figure.getByText('Veri tablosu', { exact: true })).toBeVisible();
  }
  await expect(charts.getByRole('img').first()).toBeVisible();

  // Dönem değişince rapor yenilenir (90 gün: tohumdaki 60 günlük geçmişin tamamı)
  const reloaded = mainReport(agent, 90);
  await agent.getByLabel('Dönem').selectOption('90');
  const api90 = byKey((await (await reloaded).json()) as { kpis: KpiValue[] });
  await expect(tile).toContainText(`Örneklem: ${String(api90.mttr.count)} kayıt`);
  expect(api90.mttr.count).toBeGreaterThan(api.mttr.count);

  // Talep eden panoyu göremez (API 403)
  const forbidden = await request.get(`${FAZ4_API}/kpi`, {
    headers: bearer('vatandas', 'requester'),
  });
  expect(forbidden.status()).toBe(403);
  await closeAll(agent);
});
