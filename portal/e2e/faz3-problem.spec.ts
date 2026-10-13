import { expect, test } from '@playwright/test';
import { FAZ3_API, bearer, closeAll, loginAs } from './helpers';

/**
 * Faz 3 sorun yönetimi — uçtan uca: benzer üç olay -> eğilim taraması -> sorun önerisi (R1,
 * `ai:problem`, gölge mod kapalı + güven >= eşik olduğu için otomatik) -> RCA taslağı (R1, LLM:
 * E2E backend'inde deterministik FakeLLM) görünür. Ajan sorunu çözemez/kapatamaz; kesin RCA insanın.
 */
test('benzer üç olay -> eğilim taraması -> sorun önerisi -> RCA taslağı', async ({
  browser,
  request,
}) => {
  // Çalıştırmaya özgü ortak sözcük: küme yalnızca bu üç olaydan oluşur
  const tag = `Depo${Date.now().toString(36)}`;
  const title = `${tag} el terminali barkod okuyucu bağlantısı kopuyor`;
  const numbers: string[] = [];
  for (const caller of ['ali', 'mehmet', 'son.kullanici']) {
    const response = await request.post(`${FAZ3_API}/incidents`, {
      headers: bearer('ayse', 'agent'),
      data: { title, description: `${caller}: vardiya sırasında bağlantı sık sık kopuyor`, caller },
    });
    expect(response.status(), await response.text()).toBe(201);
    numbers.push(((await response.json()) as { number: string }).number);
  }

  const agent = await loginAs(browser, 'ayse', ['agent']);
  await agent.goto('/problemler');
  const trends = agent.getByRole('region', { name: 'Eğilim analizi' });
  await trends.getByRole('button', { name: 'Eğilimleri tara' }).click();
  const cluster = trends.getByRole('listitem').filter({ hasText: tag });
  await expect(cluster).toContainText('3 olay');
  for (const n of numbers) await expect(cluster).toContainText(n);

  await trends.getByRole('button', { name: 'Sorun kayıtları öner' }).click();
  await expect(trends.getByText(/1 sorun kaydı açıldı, 0 öneri onay bekliyor/)).toBeVisible();

  // Önerilen sorun listede; ayrıntıda kanıt olaylar bağlı
  const link = agent.getByRole('link', { name: new RegExp(tag) });
  await expect(link).toBeVisible();
  await link.click();
  await expect(agent).toHaveURL(/\/problemler\/[^/?]+$/);
  for (const n of numbers) await expect(agent.getByText(n).first()).toBeVisible();

  // RCA taslağı: yapay zekâ yalnızca taslak yazar, kesin RCA boş kalır
  const rca = agent.getByRole('region', { name: 'Kök neden analizi' });
  await expect(rca.getByText('Henüz kesin kök neden analizi yok.')).toBeVisible();
  await rca.getByRole('button', { name: 'Yapay zekâ taslağı üret' }).click();
  await expect(rca.getByText('Yapay zekâ taslağı')).toBeVisible();
  await expect(rca.getByText(/5 Neden taslağı hazırladı/)).toBeVisible();
  await expect(rca.getByRole('textbox', { name: 'Kök neden' })).not.toHaveValue('');
  await expect(rca.getByText('Henüz kesin kök neden analizi yok.')).toBeVisible();
  await expect(rca.getByRole('button', { name: 'Taslağı olduğu gibi kabul et' })).toBeVisible();
  await closeAll(agent);
});
