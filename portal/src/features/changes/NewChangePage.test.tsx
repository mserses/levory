import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { ScheduleConflict } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { AGENT, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import NewChangePage from './NewChangePage';
import { makeChange, makeCI, makeFreezeWindow, makeImpact, makeTemplate } from './testUtils';

type Body = Record<string, unknown>;

const db = makeCI({ name: 'erp-db-01', ci_class: 'database', criticality: 2 });
const app = makeCI({ name: 'erp-app', ci_class: 'application', criticality: 2 });
const svc = makeCI({ name: 'Satış hizmeti', ci_class: 'service', criticality: 1 });

function mockCmdb() {
  const impactBodies: Body[] = [];
  server.use(
    http.get(apiUrl('/cmdb/cis'), ({ request }) => {
      const q = new URL(request.url).searchParams.get('q') ?? '';
      return HttpResponse.json([db, app, svc].filter((c) => c.name.includes(q)));
    }),
    http.post(apiUrl('/cmdb/impact'), async ({ request }) => {
      const body = (await request.json()) as Body;
      impactBodies.push(body);
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
    http.get(apiUrl('/changes/calendar'), () =>
      HttpResponse.json({
        changes: [],
        freeze_windows: [
          makeFreezeWindow({
            start_at: '2026-10-20T00:00:00+03:00',
            end_at: '2026-10-21T00:00:00+03:00',
            reason: 'Ay sonu kapanışı',
          }),
        ],
      }),
    ),
  );
  return impactBodies;
}

function mockCreate(conflicts: ScheduleConflict[] = []) {
  const bodies: Body[] = [];
  const created = makeChange({ id: 'chg-new', number: 'CHG0000042' });
  server.use(
    http.post(apiUrl('/changes'), async ({ request }) => {
      bodies.push((await request.json()) as Body);
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get(apiUrl('/changes/chg-new/conflicts'), () => HttpResponse.json(conflicts)),
  );
  return bodies;
}

async function setup() {
  const utils = renderWithProviders(
    <Routes>
      <Route path="/degisiklikler/yeni" element={<NewChangePage />} />
      <Route path="/degisiklikler/:id" element={<p>Ayrıntı sayfası</p>} />
    </Routes>,
    { user: AGENT, route: '/degisiklikler/yeni' },
  );
  await screen.findByRole('heading', { level: 1, name: 'Yeni değişiklik talebi' });
  return utils;
}

const setWindow = (start: string, end: string) => {
  fireEvent.change(screen.getByLabelText(/Planlanan başlangıç/), { target: { value: start } });
  fireEvent.change(screen.getByLabelText(/Planlanan bitiş/), { target: { value: end } });
};

describe('NewChangePage (/degisiklikler/yeni)', () => {
  it('CI seçimi canlı etki önizlemesi gösterir (impact_analysis)', async () => {
    const impactBodies = mockCmdb();
    const { user } = await setup();
    await user.type(screen.getByRole('searchbox', { name: 'CI ara' }), 'erp-db');
    await user.click(await screen.findByRole('button', { name: 'erp-db-01 ekle' }));

    const selected = screen.getByRole('list', { name: 'Seçili CI’lar' });
    expect(within(selected).getByText('erp-db-01')).toBeInTheDocument();
    await waitFor(() => {
      expect(impactBodies.at(-1)).toEqual({ ci_ids: [db.id], max_depth: 5 });
    });

    const preview = await screen.findByRole('region', { name: 'Etki önizlemesi' });
    expect(await within(preview).findByText('erp-app')).toBeInTheDocument();
    expect(within(preview).getByText(/Etkilenen CI: 2/)).toBeInTheDocument();
    expect(within(preview).getAllByText('K1 · Çok kritik').length).toBeGreaterThan(0);
    expect(within(preview).getByText(/İş hizmetleri: Satış hizmeti/)).toBeInTheDocument();

    await user.click(within(selected).getByRole('button', { name: 'erp-db-01 kaldır' }));
    expect(screen.queryByRole('region', { name: 'Etki önizlemesi' })).not.toBeInTheDocument();
  });

  it('standart değişiklik şablon ister ve planları şablondan doldurur', async () => {
    mockCmdb();
    const template = makeTemplate({ name: 'Sertifika yenileme', backout_plan: 'Eski sertifika' });
    let templatesUrl: URL | null = null;
    server.use(
      http.get(apiUrl('/changes/templates'), ({ request }) => {
        templatesUrl = new URL(request.url);
        return HttpResponse.json([template]);
      }),
    );
    const { user } = await setup();
    await user.click(screen.getByRole('radio', { name: /Standart/ }));
    const select = await screen.findByRole('combobox', { name: /Standart şablon/ });
    await waitFor(() => {
      expect(templatesUrl!.searchParams.get('active')).toBe('true');
    });
    await user.click(screen.getByRole('button', { name: 'Taslağı kaydet' }));
    expect(
      await screen.findByText('Standart değişiklik için bir şablon seçin.'),
    ).toBeInTheDocument();

    await user.selectOptions(select, template.id);
    expect(screen.getByRole('textbox', { name: /Geri dönüş planı/ })).toHaveValue('Eski sertifika');
  });

  it('planlanan pencere gönderimden önce çakışma için denetlenir', async () => {
    mockCmdb();
    await setup();
    setWindow('2026-10-20T10:00', '2026-10-20T12:00');
    const warning = await screen.findByRole('status', { name: 'Olası çakışmalar' });
    expect(within(warning).getByText(/Dondurma penceresi: Ay sonu kapanışı/)).toBeInTheDocument();
  });

  it('doğrulama: başlık zorunlu, bitiş başlangıçtan sonra', async () => {
    mockCmdb();
    const bodies = mockCreate();
    const { user } = await setup();
    setWindow('2026-10-22T12:00', '2026-10-22T10:00');
    await user.click(screen.getByRole('button', { name: 'Taslağı kaydet' }));
    expect(await screen.findByText('Başlık zorunludur.')).toBeInTheDocument();
    expect(screen.getByText('Planlanan bitiş, başlangıçtan sonra olmalıdır.')).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
  });

  it('mutlu yol: taslak oluşturur, sunucu çakışma denetimi temizse ayrıntıya gider', async () => {
    mockCmdb();
    const bodies = mockCreate();
    const { user } = await setup();
    await user.type(screen.getByRole('textbox', { name: /Başlık/ }), 'ERP yaması');
    await user.click(screen.getByRole('radio', { name: /Acil/ }));
    await user.type(screen.getByRole('textbox', { name: /Uygulama planı/ }), 'Yamayı uygula');
    await user.type(screen.getByRole('textbox', { name: /Geri dönüş planı/ }), 'Geri al');
    await user.type(screen.getByRole('textbox', { name: /Test planı/ }), 'Duman testi');
    await user.type(screen.getByRole('searchbox', { name: 'CI ara' }), 'erp-db');
    await user.click(await screen.findByRole('button', { name: 'erp-db-01 ekle' }));
    setWindow('2026-10-22T22:00', '2026-10-23T01:00');
    await user.click(screen.getByRole('button', { name: 'Taslağı kaydet' }));

    expect(await screen.findByText('Ayrıntı sayfası')).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({
      title: 'ERP yaması',
      change_type: 'emergency',
      implementation_plan: 'Yamayı uygula',
      backout_plan: 'Geri al',
      test_plan: 'Duman testi',
      affected_ci_ids: [db.id],
      planned_start: '2026-10-22T19:00:00.000Z',
      planned_end: '2026-10-22T22:00:00.000Z',
    });
    expect(bodies[0]).not.toHaveProperty('template_id');
  });

  it('sunucu çakışma bulursa taslak kaydedilir ve çakışmalar gösterilir', async () => {
    mockCmdb();
    mockCreate([
      {
        kind: 'change',
        ref_id: 'chg-x',
        ref_number: 'CHG0000009',
        start: '2026-10-22T19:00:00Z',
        end: '2026-10-22T21:00:00Z',
        description: 'Aynı CI üzerinde zamanlanmış değişiklik',
      },
    ]);
    const { user } = await setup();
    await user.type(screen.getByRole('textbox', { name: /Başlık/ }), 'ERP yaması');
    setWindow('2026-10-22T22:00', '2026-10-23T01:00');
    await user.click(screen.getByRole('button', { name: 'Taslağı kaydet' }));
    const dialog = await screen.findByRole('alertdialog', { name: /çakışma/i });
    expect(within(dialog).getByText(/CHG0000009/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Taslağa git' }));
    expect(await screen.findByText('Ayrıntı sayfası')).toBeInTheDocument();
  });

  it('sunucu doğrulama hatası (422) formda gösterilir', async () => {
    mockCmdb();
    server.use(
      http.post(apiUrl('/changes'), () =>
        HttpResponse.json({ detail: 'Bilinmeyen atama grubu' }, { status: 422 }),
      ),
    );
    const { user } = await setup();
    await user.type(screen.getByRole('textbox', { name: /Başlık/ }), 'ERP yaması');
    await user.click(screen.getByRole('button', { name: 'Taslağı kaydet' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Bilinmeyen atama grubu');
  });
});
