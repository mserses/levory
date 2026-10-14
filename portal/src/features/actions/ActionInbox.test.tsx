import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { ActionRequest } from '@/api/types';
import ApprovalsPage from '@/features/approvals/ApprovalsPage';
import { makeActionRequest } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { APPROVER, renderWithProviders, type TestUser } from '@/test/render';
import { blockReason, isExpired, progressLabel } from './rules';

const CHANGE_MANAGER: TestUser = { username: 'dm.deniz', roles: ['change_manager'] };

const r2 = makeActionRequest({
  id: 'act-r2',
  number: 'ACT0000010',
  tool: 'incident.resolve',
  tool_description: 'Olayı çözüldü olarak işaretler.',
  risk_class: 'R2',
  requested_by: 'ai:service_desk',
  reason: 'Kullanıcı sorunun geçtiğini <b>doğruladı</b>.',
  confidence: 0.82,
  args_display: { incident_id: 'inc-1', resolution: 'Yeniden başlatıldı; arayan: ***' },
  incident_id: 'inc-1',
});
const r3 = makeActionRequest({
  id: 'act-r3',
  number: 'ACT0000011',
  tool: 'identity.add_to_group',
  tool_description: 'Kullanıcıyı gruba ekler.',
  risk_class: 'R3',
  required_approvals: 2,
  requested_by: 'ai:fulfilment',
  args_display: { user: 'm***@kurum.com.tr', group: 'sg-domain-admins' },
  request_id: 'req-1',
  approvals: [
    {
      id: 'ap-1',
      approver: 'guvenlik.ayse',
      approver_roles: ['approver'],
      decision: 'approve',
      comment: 'Yönetici onayı var',
      created_at: '2026-10-12T10:00:00Z',
    },
  ],
});

function mockActions(list: ActionRequest[], single: ActionRequest[] = []) {
  const seen: URLSearchParams[] = [];
  const decisions: { id: string; kind: string; body: unknown }[] = [];
  server.use(
    http.get(apiUrl('/actions'), ({ request }) => {
      const params = new URL(request.url).searchParams;
      seen.push(params);
      const state = params.get('state');
      return HttpResponse.json(state ? list.filter((a) => a.state === state) : list);
    }),
    http.get(apiUrl('/actions/:id'), ({ params }) => {
      const found = [...list, ...single].find((a) => a.id === params.id);
      return found
        ? HttpResponse.json(found)
        : HttpResponse.json({ detail: 'Eylem isteği bulunamadı' }, { status: 404 });
    }),
  );
  return { seen, decisions };
}

function mockDecision(kind: 'approve' | 'reject', respond: (a: ActionRequest) => Response) {
  const bodies: { id: string; body: unknown }[] = [];
  server.use(
    http.post(apiUrl(`/actions/:id/${kind}`), async ({ params, request }) => {
      bodies.push({ id: String(params.id), body: await request.json() });
      const a = [r2, r3].find((x) => x.id === params.id) ?? r2;
      return respond(a);
    }),
  );
  return bodies;
}

const renderTab = (user: TestUser = APPROVER, extra = '') =>
  renderWithProviders(<ApprovalsPage />, { user, route: `/onaylar?sekme=eylem${extra}` });

const card = async (number: string) => screen.findByRole('article', { name: new RegExp(number) });

describe('/onaylar — Eylem onayları sekmesi', () => {
  it('bekleyen istekler: risk rozeti, araç açıklaması, maskeli argümanlar, gerekçe, bağlar, onay sayısı', async () => {
    const { seen } = mockActions([r2, r3]);
    renderTab();
    expect(await screen.findByRole('tab', { name: 'Eylem onayları' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const el = await card('ACT0000010');
    expect(seen[0]?.get('state')).toBe('pending');
    const c = within(el);
    expect(c.getByRole('heading', { level: 3 })).toHaveTextContent(
      'ACT0000010 Olayı çözüldü olarak işaretler.',
    );
    expect(c.getByText('R2 · Orta risk')).toBeInTheDocument();
    expect(c.getByText('incident.resolve')).toBeInTheDocument();
    expect(c.getByText(/Otomasyon \(ai:service_desk\)/)).toBeInTheDocument();
    // Gerekçe düz metin
    expect(c.getByText(/Kullanıcı sorunun geçtiğini/)).toHaveTextContent('<b>doğruladı</b>');
    expect(c.getByText('Yeniden başlatıldı; arayan: ***')).toBeInTheDocument();
    expect(c.getByRole('link', { name: 'Olay kaydı' })).toHaveAttribute('href', '/kayitlar/inc-1');
    expect(c.getByText('(R2: 0/1 onay)')).toBeInTheDocument();

    const e3 = within(await card('ACT0000011'));
    expect(e3.getByText('R3 · Kritik')).toBeInTheDocument();
    expect(e3.getByText('(R3: 1/2 onay)')).toBeInTheDocument();
    expect(e3.getByText('guvenlik.ayse')).toBeInTheDocument();
    expect(e3.getByText(/Yönetici onayı var/)).toBeInTheDocument();
    expect(e3.getByRole('link', { name: 'Hizmet talebi' })).toHaveAttribute(
      'href',
      '/taleplerim/req-1',
    );
    expect(e3.getByText('m***@kurum.com.tr')).toBeInTheDocument();
  });

  it('öz onay engellenir (büyük/küçük harf ve boşluk duyarsız) ve nedeni açıklanır', async () => {
    mockActions([{ ...r2, requested_by: '  SEF.Ali ' }]);
    renderTab();
    const c = within(await card('ACT0000010'));
    const approve = c.getByRole('button', { name: 'Onayla' });
    expect(approve).toBeDisabled();
    expect(c.getByRole('button', { name: 'Reddet' })).toBeDisabled();
    expect(approve).toHaveAccessibleDescription(/kendi isteğinizi onaylayamaz/);
    expect(c.queryByRole('textbox', { name: /Yorum/ })).toBeNull();
  });

  it('aynı kişi ikinci kez karar veremez', async () => {
    mockActions([
      {
        ...r3,
        approvals: [{ ...(r3.approvals ?? [])[0]!, approver: 'sef.ali' }],
      },
    ]);
    renderTab();
    const c = within(await card('ACT0000011'));
    expect(c.getByRole('button', { name: 'Onayla' })).toHaveAccessibleDescription(
      /zaten karar verdiniz/,
    );
  });

  it('süresi dolan istek "Süresi doldu" olarak gösterilir ve onaylanamaz', async () => {
    mockActions([{ ...r2, expires_at: '2020-01-01T00:00:00Z' }]);
    renderTab();
    const c = within(await card('ACT0000010'));
    expect(c.getByText('Süresi doldu')).toBeInTheDocument();
    expect(c.getByText(/artık onaylanamaz/, { selector: 'p:not([data-blocked])' })).toBeVisible();
    expect(c.getByRole('button', { name: 'Onayla' })).toBeDisabled();
  });

  it('R2 onay: onay diyaloğu "Onayla ve yürüt" → yorumla POST → yürütüldü bildirimi', async () => {
    mockActions([r2]);
    const bodies = mockDecision('approve', (a) =>
      HttpResponse.json({
        ...a,
        state: 'executed',
        executed_by: 'sef.ali',
        executed_at: '2026-10-13T10:00:00Z',
      }),
    );
    const { user } = renderTab();
    const c = within(await card('ACT0000010'));
    await user.type(c.getByRole('textbox', { name: /Yorum/ }), 'Kontrol ettim');
    await user.click(c.getByRole('button', { name: 'Onayla' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent(/sizin kimliğinizle hemen yürütülecek/);
    await user.click(within(dialog).getByRole('button', { name: 'Onayla ve yürüt' }));
    await waitFor(() => {
      expect(bodies).toEqual([{ id: 'act-r2', body: { comment: 'Kontrol ettim' } }]);
    });
    expect(await screen.findByText('ACT0000010 onaylandı ve yürütüldü')).toBeInTheDocument();
  });

  it('R3 ilk onay: kaydedilir, ilerleme bildirilir', async () => {
    mockActions([{ ...r3, approvals: [] }]);
    mockDecision('approve', (a) =>
      HttpResponse.json({
        ...a,
        approvals: [{ ...(r3.approvals ?? [])[0]!, approver: 'sef.ali' }],
      }),
    );
    const { user } = renderTab();
    const c = within(await card('ACT0000011'));
    await user.click(c.getByRole('button', { name: 'Onayla' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('(1/2)');
    await user.click(within(dialog).getByRole('button', { name: 'Onayımı kaydet' }));
    expect(await screen.findByText('Onayınız kaydedildi (R3: 1/2 onay)')).toBeInTheDocument();
  });

  it('red gerekçe ister; sonra POST reject', async () => {
    mockActions([r2]);
    const bodies = mockDecision('reject', (a) => HttpResponse.json({ ...a, state: 'rejected' }));
    const { user } = renderTab();
    const c = within(await card('ACT0000010'));
    await user.click(c.getByRole('button', { name: 'Reddet' }));
    expect(c.getByRole('textbox', { name: /Yorum/ })).toHaveAccessibleDescription(
      /Reddetmek için gerekçe yazın/,
    );
    expect(bodies).toHaveLength(0);
    await user.type(c.getByRole('textbox', { name: /Yorum/ }), 'Kullanıcı hâlâ etkileniyor');
    await user.click(c.getByRole('button', { name: 'Reddet' }));
    await waitFor(() => {
      expect(bodies).toEqual([{ id: 'act-r2', body: { comment: 'Kullanıcı hâlâ etkileniyor' } }]);
    });
    expect(await screen.findByText('ACT0000010 reddedildi')).toBeInTheDocument();
  });

  it('409 (süresi doldu / karar beklemiyor) hata bildirimi gösterir', async () => {
    mockActions([r2]);
    mockDecision('approve', () =>
      HttpResponse.json({ detail: 'Eylem isteğinin süresi doldu' }, { status: 409 }),
    );
    const { user } = renderTab();
    const c = within(await card('ACT0000010'));
    await user.click(c.getByRole('button', { name: 'Onayla' }));
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Onayla ve yürüt',
      }),
    );
    expect(await screen.findByText('İstek değişti')).toBeInTheDocument();
    expect(screen.getByText('Eylem isteğinin süresi doldu')).toBeInTheDocument();
  });

  it('change_manager: yalnızca eylem sekmesi; R2 kararı yetkisiz, değişikliğe bağlı R3 serbest', async () => {
    let decisionsCalled = false;
    server.use(
      http.get(apiUrl('/incidents/decisions'), () => {
        decisionsCalled = true;
        return HttpResponse.json([]);
      }),
    );
    mockActions([r2, { ...r3, change_id: 'chg-1', request_id: null }]);
    renderWithProviders(<ApprovalsPage />, { user: CHANGE_MANAGER, route: '/onaylar' });
    const e2 = within(await card('ACT0000010'));
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Eylem onayları']);
    expect(e2.getByRole('button', { name: 'Onayla' })).toHaveAccessibleDescription(
      /karar verme yetkiniz yok/,
    );
    const e3 = within(await card('ACT0000011'));
    expect(e3.getByRole('button', { name: 'Onayla' })).toBeEnabled();
    expect(e3.getByText(/CAB onaylıysa tek onay yeterlidir/)).toBeInTheDocument();
    expect(e3.getByRole('link', { name: 'Değişiklik kaydı' })).toHaveAttribute(
      'href',
      '/degisiklikler/chg-1',
    );
    expect(decisionsCalled).toBe(false);
  });

  it('bağlantıdaki istek listede değilse ayrıca yüklenip vurgulanır; durum süzgeci', async () => {
    const old = makeActionRequest({ id: 'act-old', number: 'ACT0000001', state: 'executed' });
    const { seen } = mockActions([r2], [old]);
    const { user } = renderTab(APPROVER, '&istek=act-old');
    const el = await card('ACT0000001');
    expect(el).toHaveAttribute('data-highlight', 'true');
    expect(within(el).queryByRole('button', { name: 'Onayla' })).toBeNull();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Durum' }), 'all');
    await waitFor(() => {
      expect(seen.at(-1)?.has('state')).toBe(false);
    });
  });
});

describe('eylem kuralları', () => {
  const who = (username: string, roles: string[]) => ({
    username,
    hasRole: (...r: string[]) => roles.includes('admin') || r.some((x) => roles.includes(x)),
  });
  it('ilerleme etiketi ve süre aşımı', () => {
    expect(progressLabel(r3)).toBe('R3: 1/2 onay');
    expect(isExpired({ ...r2, state: 'expired' })).toBe(true);
    expect(isExpired(r2, new Date('2099-01-02T00:00:00Z'))).toBe(true);
    expect(isExpired(r2, new Date('2026-10-13T00:00:00Z'))).toBe(false);
  });
  it('karar engelleri', () => {
    expect(blockReason(r2, who('AI:SERVICE_DESK', ['approver']))).toBe('self');
    expect(blockReason(r2, who('x', ['cab']))).toBe('role');
    expect(blockReason(r2, who('x', ['admin']))).toBeNull();
    expect(blockReason({ ...r2, state: 'executed' }, who('x', ['approver']))).toBe('not_pending');
  });
});
