import { expect, test } from '@playwright/test';
import { closeAll, loginAs, unique } from './helpers';

/**
 * Self-servis yalıtımı (gerçek backend): son kullanıcı yalnızca kendi taleplerini görür,
 * başkasının kaydına ve yapay zekâ kararlarına API'den de ulaşamaz; arayanı taklit edemez.
 */
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8765'}`;
const bearer = (user: string, roles: string) => ({ Authorization: `Bearer dev:${user}:${roles}` });

test('requester başkasının kaydını göremez, arayanı taklit edemez', async ({
  browser,
  request,
}) => {
  const title = unique('Başkasının yazıcısı');
  // Mehmet kendi adına talep açar (arayan alanını "zeynep" göndermeyi dener)
  const created = await request.post(`${API}/incidents`, {
    headers: bearer('mehmet', 'requester'),
    data: { title, description: 'Yazıcı çalışmıyor', caller: 'zeynep', impact: 'high' },
  });
  expect(created.status()).toBe(201);
  const incident = (await created.json()) as { id: string; caller: string; impact: string };
  expect(incident.caller).toBe('mehmet');
  expect(incident.impact).toBe('low');

  // requester + approver (agent değil) da başkası adına kayıt açamaz
  const mixed = await request.post(`${API}/incidents`, {
    headers: bearer('sef.ali', 'requester,approver'),
    data: { title: unique('Karma rol'), caller: 'genel.mudur', impact: 'high', urgency: 'high' },
  });
  expect(mixed.status()).toBe(201);
  expect(await mixed.json()).toMatchObject({ caller: 'sef.ali', impact: 'low', priority: 'P3' });

  // Uzman sınıflandırır (karar oluşur)
  const triage = await request.post(`${API}/incidents/${incident.id}/triage`, {
    headers: bearer('ayse', 'agent'),
  });
  expect(triage.ok()).toBeTruthy();

  // Zeynep arayüzde Mehmet'in talebini görmez
  const zeynep = await loginAs(browser, 'zeynep', ['requester']);
  await zeynep.goto('/taleplerim');
  await expect(zeynep.getByRole('heading', { level: 1, name: 'Taleplerim' })).toBeVisible();
  await zeynep.getByLabel('Göster').selectOption('all');
  await expect(zeynep.getByText(title)).toHaveCount(0);
  // Uzman ekranı (doğrudan bağlantı) yetkisiz sayfası gösterir
  await zeynep.goto(`/kayitlar/${incident.id}`);
  await expect(
    zeynep.getByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
  ).toBeVisible();

  // API: başkasının kaydı 404 (varlığı sızdırılmaz), kararlar ve karar kutusu 403
  const asZeynep = bearer('zeynep', 'requester');
  expect(
    (await request.get(`${API}/incidents/${incident.id}`, { headers: asZeynep })).status(),
  ).toBe(404);
  expect(
    (
      await request.get(`${API}/incidents/${incident.id}/decisions`, { headers: asZeynep })
    ).status(),
  ).toBe(403);
  expect((await request.get(`${API}/incidents/decisions`, { headers: asZeynep })).status()).toBe(
    403,
  );
  expect((await request.get(`${API}/audit`, { headers: asZeynep })).status()).toBe(403);
  expect((await request.get(`${API}/knowledge/articles`, { headers: asZeynep })).status()).toBe(
    403,
  );
  const list = await request.get(`${API}/incidents?mine=false&limit=500`, { headers: asZeynep });
  const rows = (await list.json()) as { caller: string; ai_confidence: number | null }[];
  expect(rows.every((r) => r.caller === 'zeynep')).toBe(true);

  // Mehmet kendi kaydını görür ama yapay zekâ iç ayrıntısını (güven) görmez
  const own = await request.get(`${API}/incidents/${incident.id}`, {
    headers: bearer('mehmet', 'requester'),
  });
  expect(own.status()).toBe(200);
  expect(((await own.json()) as { ai_confidence: unknown }).ai_confidence).toBeNull();

  await closeAll(zeynep);
});
