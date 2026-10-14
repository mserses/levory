import { expect, test, type APIRequestContext } from '@playwright/test';
import { FAZ4_API, FAZ4_EVENTS_SECRET, bearer, closeAll, loginAs } from './helpers';

/**
 * Faz 4 izleme ve olay yönetimi — uçtan uca: Alertmanager webhook'u (v4) bir veritabanı CI'sında ve
 * ona bağımlı uygulamada ateşleyen uyarıları getirir -> CMDB bağımlılık grafiğiyle TEK bağıntılı
 * uyarı grubu (kök CI = veritabanı) -> olay ajanı (`ai:events`) tek olay önerir; öncelik P2 (CI
 * kritikliği 2 + kritik önem) olduğu için R2: insan onayı bekler, onaylanınca onaylayanın kimliğiyle
 * açılır -> uyarılar çözülünce olaya çalışma notu eklenir ve çözüm yalnızca ÖNERİLİR (R2, otomatik
 * çözüm yok). Gölge mod kapalı faz4 backend'i; webhook paylaşılan gizliyle doğrulanır.
 */

const CHANGE_MANAGER = bearer('mudur.e2e', 'change_manager');
const ADMIN = bearer('yonetici', 'admin');
const APPROVER = bearer('sef.ali', 'approver');

interface WebhookResult {
  received: number;
  created: number;
  updated: number;
  group_ids: string[];
  actions: { action: string; status?: string; action_request_id?: string; incident_id?: string }[];
}

async function createCi(
  request: APIRequestContext,
  name: string,
  ciClass: string,
  criticality: number,
): Promise<string> {
  const response = await request.post(`${FAZ4_API}/cmdb/cis`, {
    headers: CHANGE_MANAGER,
    data: { name, ci_class: ciClass, environment: 'prod', criticality },
  });
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()) as { id: string }).id;
}

function alert(fingerprint: string, ci: string, status: 'firing' | 'resolved', startsAt: Date) {
  return {
    status,
    labels: { alertname: `${ci}-HighErrorRate`, severity: 'critical', ci },
    annotations: { summary: `${ci} hata oranı yüksek` },
    startsAt: startsAt.toISOString(),
    endsAt: status === 'resolved' ? new Date().toISOString() : '0001-01-01T00:00:00Z',
    generatorURL: 'http://prometheus.local/graph',
    fingerprint,
  };
}

async function postAlerts(
  request: APIRequestContext,
  status: 'firing' | 'resolved',
  alerts: ReturnType<typeof alert>[],
): Promise<WebhookResult> {
  const response = await request.post(`${FAZ4_API}/events/alertmanager`, {
    headers: { Authorization: `Bearer ${FAZ4_EVENTS_SECRET}` },
    data: {
      version: '4',
      groupKey: `{}:{e2e="${status}"}`,
      truncatedAlerts: 0,
      status,
      receiver: 'serviszeka',
      groupLabels: {},
      commonLabels: {},
      commonAnnotations: {},
      externalURL: 'http://alertmanager.local',
      alerts,
    },
  });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as WebhookResult;
}

test('Alertmanager: DB + bağımlı uygulama -> tek grup -> tek olay (P2, onay) -> çözülen uyarı not ekler', async ({
  browser,
  request,
}) => {
  const suffix = Date.now().toString(36);
  const dbName = `e2e-odeme-db-${suffix}`;
  const appName = `e2e-odeme-api-${suffix}`;
  const dbId = await createCi(request, dbName, 'database', 2);
  const appId = await createCi(request, appName, 'application', 3);
  const rel = await request.post(`${FAZ4_API}/cmdb/relationships`, {
    headers: CHANGE_MANAGER,
    data: { source_id: appId, target_id: dbId, type: 'depends_on' },
  });
  expect(rel.status(), await rel.text()).toBe(201);

  // Kimliksiz webhook reddedilir
  const anonymous = await request.post(`${FAZ4_API}/events/alertmanager`, { data: {} });
  expect(anonymous.status()).toBe(401);

  // 1) Uygulama ve veritabanı uyarısı aynı teslimatta: tek grup, kök CI veritabanı
  const startsAt = new Date(Date.now() - 2 * 60_000);
  const appAlert = alert(`fp-app-${suffix}`, appName, 'firing', startsAt);
  const dbAlert = alert(`fp-db-${suffix}`, dbName, 'firing', startsAt);
  const fired = await postAlerts(request, 'firing', [appAlert, dbAlert]);
  expect(fired.created).toBe(2);
  expect(fired.group_ids).toHaveLength(1);
  const groupId = fired.group_ids[0];
  const proposal = fired.actions.find((a) => a.action === 'create');
  expect(proposal?.status).toBe('pending_approval');
  const actionId = proposal?.action_request_id ?? '';
  expect(actionId).not.toBe('');

  // Onaysız olay açılmadı (P2 = insan kararı)
  const action = await request.get(`${FAZ4_API}/actions/${actionId}`, { headers: APPROVER });
  const actionBody = (await action.json()) as { number: string; risk_class: string; tool: string };
  expect(actionBody.tool).toBe('event.create_incident');
  expect(actionBody.risk_class).toBe('R2');

  // 2) Destek uzmanı: liste -> grup ayrıntısı (kök CI, bağımlılık yolu, P2 önerisi, onay bekliyor)
  const agent = await loginAs(browser, 'ayse', ['agent']);
  await agent.goto('/olaylar');
  const row = agent.getByRole('row').filter({ hasText: dbName });
  await expect(row).toHaveCount(1); // tek bağıntılı grup
  await row.locator(`a[href$="/olaylar/${groupId}"]`).click();
  await expect(agent).toHaveURL(new RegExp(`/olaylar/${groupId}$`));
  const summary = agent.getByRole('region', { name: 'Özet' });
  await expect(summary.getByRole('link', { name: dbName })).toBeVisible();
  await expect(summary.getByText('P2', { exact: false }).first()).toBeVisible();
  await expect(agent.getByText('Olay açma önerisi insan onayı bekliyor.')).toBeVisible();
  const members = agent.getByRole('region', { name: 'Bağıntılı uyarılar' });
  await expect(members.getByRole('row')).toHaveCount(3); // başlık + 2 uyarı
  await expect(members).toContainText(`${dbName} → ${appName}`); // kök CI -> etkilenen

  // Üstlen (R1, insan kimliğiyle hemen)
  await agent.getByRole('button', { name: 'Üstlen' }).click();
  await expect(summary.getByText('Üstlenen: ayse')).toBeVisible();

  // Sustur (R2): yalnızca eylem isteği açılır, grup onaysız susturulmaz
  await agent.getByRole('button', { name: 'Sustur…' }).click();
  const silence = agent.getByRole('dialog', { name: 'Uyarı grubunu sustur' });
  await silence.getByLabel('Gerekçe').fill('Planlı bakım penceresi');
  await silence.getByRole('button', { name: 'Onaya gönder' }).click();
  await expect(agent.getByRole('status', { name: 'Susturma onay bekliyor' })).toBeVisible();
  const silenceRequests = await request.get(
    `${FAZ4_API}/actions?state=pending&tool=event.silence`,
    { headers: APPROVER },
  );
  const pendingSilence = (
    (await silenceRequests.json()) as {
      requested_by: string;
      risk_class: string;
      args_display: { group_id?: string };
    }[]
  ).filter((a) => a.args_display.group_id === groupId);
  expect(pendingSilence).toHaveLength(1);
  expect(pendingSilence[0]?.requested_by).toBe('ayse');
  expect(pendingSilence[0]?.risk_class).toBe('R2');

  // 3) Onaylayan olay açmayı onaylar: olay onaylayanın kimliğiyle, P2
  const approver = await loginAs(browser, 'sef.ali', ['approver']);
  await approver.goto(`/onaylar?sekme=eylem&istek=${actionId}`);
  const card = approver.locator(`[data-action-id="${actionId}"]`);
  await expect(card).toContainText('R2');
  await card.getByRole('button', { name: 'Onayla' }).click();
  await approver
    .getByRole('alertdialog', { name: `${actionBody.number} onaylansın mı?` })
    .getByRole('button', { name: 'Onayla ve yürüt' })
    .click();
  await expect
    .poll(async () => {
      const r = await request.get(`${FAZ4_API}/actions/${actionId}`, { headers: APPROVER });
      return ((await r.json()) as { state: string }).state;
    })
    .toBe('executed');

  const groupResponse = await request.get(`${FAZ4_API}/events/groups/${groupId}`, {
    headers: bearer('ayse', 'agent'),
  });
  const detail = (await groupResponse.json()) as {
    group: { incident_id: string | null; acknowledged_by: string | null };
  };
  const incidentId = detail.group.incident_id ?? '';
  expect(incidentId).not.toBe('');
  const incidentResponse = await request.get(`${FAZ4_API}/incidents/${incidentId}`, {
    headers: bearer('ayse', 'agent'),
  });
  const incident = (await incidentResponse.json()) as {
    number: string;
    priority: string;
    status: string;
    title: string;
  };
  expect(incident.priority).toBe('P2');
  expect(incident.title).toContain(dbName);

  // Ayrıntı sayfası olaya bağlanır; yeni teslimat ikinci olay açmaz
  await agent.reload();
  const incidentLink = summary.getByRole('link', { name: 'Olay kaydı' });
  await expect(incidentLink).toHaveAttribute('href', new RegExp(`/kayitlar/${incidentId}$`));

  // 4) Uyarılar çözülür: aynı olaya çalışma notu + çözüm ÖNERİSİ (R2), olay açık kalır
  const resolved = await postAlerts(request, 'resolved', [
    alert(`fp-app-${suffix}`, appName, 'resolved', startsAt),
    alert(`fp-db-${suffix}`, dbName, 'resolved', startsAt),
  ]);
  expect(resolved.group_ids).toEqual([groupId]);
  expect(resolved.updated).toBe(2);
  const propose = resolved.actions.find((a) => a.action === 'propose_resolve');
  expect(propose?.status).toBe('pending_approval');

  const audit = await request.get(`${FAZ4_API}/audit?limit=100`, { headers: ADMIN });
  const entries = (
    (await audit.json()) as {
      items: { action: string; actor: string; entity_id: string; details: { note?: string } }[];
    }
  ).items.filter((e) => e.action === 'incident.work_note' && e.entity_id === incidentId);
  expect(entries).toHaveLength(1);
  expect(entries[0]?.actor).toBe('ai:events');
  expect(entries[0]?.details.note ?? '').not.toBe('');

  const after = await request.get(`${FAZ4_API}/incidents/${incidentId}`, {
    headers: bearer('ayse', 'agent'),
  });
  expect(((await after.json()) as { status: string }).status).not.toBe('resolved');
  const incidents = await request.get(`${FAZ4_API}/incidents?limit=200`, {
    headers: bearer('ayse', 'agent'),
  });
  const linked = ((await incidents.json()) as { title: string }[]).filter((i) =>
    i.title.includes(dbName),
  );
  expect(linked).toHaveLength(1);

  await agent.reload();
  await expect(summary.getByText('Çözüldü').first()).toBeVisible();
  await closeAll(agent, approver);
});
