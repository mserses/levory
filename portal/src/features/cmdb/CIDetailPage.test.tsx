import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { CIRelationship, ConfigurationItem } from '@/api/types';
import { makeChange, makeCI, makeImpact } from '@/features/changes/testUtils';
import { makeIncident } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { AGENT, renderWithProviders, type TestUser } from '@/test/render';
import { server } from '@/test/server';
import CIDetailPage from './CIDetailPage';

const db = makeCI({
  id: 'ci-db',
  name: 'erp-db-01',
  ci_class: 'database',
  criticality: 2,
  owner_group: 'dba',
  description: 'ERP üretim veritabanı',
  attributes: { surum: 'PostgreSQL 16' },
});
const app = makeCI({ id: 'ci-app', name: 'erp-app', ci_class: 'application', criticality: 2 });
const svc = makeCI({ id: 'ci-svc', name: 'Satış hizmeti', ci_class: 'service', criticality: 1 });
const host = makeCI({ id: 'ci-host', name: 'vm-db-01', ci_class: 'server', criticality: 3 });

const rel = (
  source: ConfigurationItem,
  target: ConfigurationItem,
  type: CIRelationship['type'],
): CIRelationship => ({
  id: `rel-${source.id}-${target.id}`,
  source_id: source.id,
  target_id: target.id,
  type,
  created_by: 'mudur',
  created_at: '2026-10-01T09:00:00Z',
});

function mockCI() {
  const impactCalls: URL[] = [];
  const changeCalls: URL[] = [];
  let incidentCalls = 0;
  const linked = makeChange({
    number: 'CHG0000077',
    title: 'Veritabanı yaması',
    state: 'scheduled',
  });
  server.use(
    ...[db, app, svc, host].map((ci) =>
      http.get(apiUrl(`/cmdb/cis/${ci.id}`), () => HttpResponse.json(ci)),
    ),
    http.get(apiUrl('/cmdb/cis/ci-db/relationships'), () =>
      HttpResponse.json([rel(app, db, 'depends_on'), rel(db, host, 'runs_on')]),
    ),
    http.get(apiUrl('/cmdb/cis/ci-db/impact'), ({ request }) => {
      impactCalls.push(new URL(request.url));
      return HttpResponse.json(
        makeImpact(
          [db],
          [
            { ci: app, depth: 1, path: [db.id, app.id] },
            { ci: svc, depth: 2, path: [db.id, app.id, svc.id] },
          ],
        ),
      );
    }),
    // Grafikten gidilen CI (erp-app) için boş ilişki/etki
    http.get(apiUrl('/cmdb/cis/ci-app/relationships'), () => HttpResponse.json([])),
    http.get(apiUrl('/cmdb/cis/ci-app/impact'), () => HttpResponse.json(makeImpact([app]))),
    http.get(apiUrl('/changes'), ({ request }) => {
      changeCalls.push(new URL(request.url));
      return HttpResponse.json([linked]);
    }),
    http.get(apiUrl('/incidents'), () => {
      incidentCalls += 1;
      return HttpResponse.json([
        makeIncident({ number: 'INC0000100', title: 'ERP yavaş', ci_ids: [db.id] }),
        makeIncident({ number: 'INC0000101', title: 'Yazıcı', ci_ids: [] }),
      ]);
    }),
  );
  return { impactCalls, changeCalls, incidentCalls: () => incidentCalls };
}

function renderCI(user: TestUser = AGENT) {
  return renderWithProviders(
    <Routes>
      <Route path="/cmdb/:id" element={<CIDetailPage />} />
    </Routes>,
    { user, route: '/cmdb/ci-db' },
  );
}

describe('CIDetailPage (/cmdb/:id)', () => {
  it('CI alanlarını Türkçe etiketlerle gösterir', async () => {
    mockCI();
    renderCI();
    expect(await screen.findByRole('heading', { level: 1, name: 'erp-db-01' })).toBeInTheDocument();
    const fields = screen.getByRole('region', { name: 'Yapılandırma öğesi' });
    expect(within(fields).getByText('Veritabanı')).toBeInTheDocument();
    expect(within(fields).getByText('K2 · Kritik')).toBeInTheDocument();
    expect(within(fields).getByText('ERP üretim veritabanı')).toBeInTheDocument();
    expect(within(fields).getByText('surum')).toBeInTheDocument();
    expect(within(fields).getByText('PostgreSQL 16')).toBeInTheDocument();
  });

  it('ilişkiler iki yönlü, adlarıyla ve Türkçe ilişki türüyle', async () => {
    mockCI();
    renderCI();
    const region = await screen.findByRole('region', { name: 'İlişkiler' });
    const appRow = (await within(region).findByRole('link', { name: 'erp-app' })).closest('tr')!;
    expect(within(appRow).getByText('bağımlıdır')).toBeInTheDocument();
    expect(within(appRow).getByText('erp-db-01')).toBeInTheDocument();
    const hostRow = (await within(region).findByRole('link', { name: 'vm-db-01' })).closest('tr')!;
    expect(within(hostRow).getByText('üzerinde çalışır')).toBeInTheDocument();
  });

  it('erişilebilir etki grafiği: derinliğe göre düğümler, kritiklik metni, klavye ile gezinme', async () => {
    mockCI();
    renderWithProviders(
      <Routes>
        <Route path="/cmdb/:id" element={<CIDetailPage />} />
      </Routes>,
      { route: '/cmdb/ci-db' },
    );
    const graph = await screen.findByRole('group', { name: /Etki grafiği/ });
    const appNode = await within(graph).findByRole('link', { name: /erp-app/ });
    expect(appNode).toHaveAccessibleName(/K2 · Kritik/);
    expect(appNode).toHaveAccessibleName(/doğrudan/i);
    expect(appNode).toHaveAttribute('tabindex', '0');
    expect(appNode).toHaveAttribute('data-depth', '1');
    const svcNode = within(graph).getByRole('link', { name: /Satış hizmeti/ });
    expect(svcNode).toHaveAttribute('data-depth', '2');
    expect(svcNode).toHaveAttribute('data-criticality', '1');
    // Renk tek başına anlam taşımaz: düğümde kritiklik metni var
    expect(within(svcNode).getByText('K1')).toBeInTheDocument();

    // Tablo yedeği
    const table = screen.getByRole('table', { name: /Etkilenen öğeler/ });
    expect(within(table).getByRole('link', { name: 'Satış hizmeti' })).toBeInTheDocument();

    // Klavye: Enter ile CI'a gider
    appNode.focus();
    fireEvent.keyDown(appNode, { key: 'Enter' });
    expect(await screen.findByRole('heading', { level: 1, name: 'erp-app' })).toBeInTheDocument();
  });

  it('derinlik değişince etki yeniden hesaplanır', async () => {
    const api = mockCI();
    const { user } = renderCI();
    await screen.findByRole('group', { name: /Etki grafiği/ });
    await user.selectOptions(screen.getByLabelText('Derinlik'), '2');
    await waitFor(() => {
      expect(api.impactCalls.at(-1)!.searchParams.get('max_depth')).toBe('2');
    });
  });

  it('bağlı değişiklikler ve olaylar', async () => {
    const api = mockCI();
    renderCI(AGENT);
    const changes = await screen.findByRole('region', { name: 'Bağlı değişiklikler' });
    expect(await within(changes).findByText(/CHG0000077/)).toBeInTheDocument();
    expect(api.changeCalls[0]!.searchParams.get('ci_id')).toBe('ci-db');
    const incidents = screen.getByRole('region', { name: 'Bağlı olaylar' });
    expect(await within(incidents).findByText(/INC0000100/)).toBeInTheDocument();
    expect(within(incidents).queryByText(/INC0000101/)).not.toBeInTheDocument();
  });

  it('CAB üyesi olay listesini görmez (olay yetkisi yok), istek de atılmaz', async () => {
    const api = mockCI();
    renderCI({ username: 'kurul1', roles: ['cab'] });
    const incidents = await screen.findByRole('region', { name: 'Bağlı olaylar' });
    expect(within(incidents).getByText(/olay kayıtlarını görme yetkiniz yok/i)).toBeInTheDocument();
    await screen.findByRole('region', { name: 'Bağlı değişiklikler' });
    expect(api.incidentCalls()).toBe(0);
  });

  it('bulunamayan CI', async () => {
    server.use(
      http.get(apiUrl('/cmdb/cis/yok'), () =>
        HttpResponse.json({ detail: 'Yapılandırma öğesi bulunamadı' }, { status: 404 }),
      ),
    );
    renderWithProviders(
      <Routes>
        <Route path="/cmdb/:id" element={<CIDetailPage />} />
      </Routes>,
      { route: '/cmdb/yok' },
    );
    expect(
      await screen.findByRole('heading', { name: 'Yapılandırma öğesi bulunamadı' }),
    ).toBeInTheDocument();
  });
});
