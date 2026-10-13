import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { Change, ChangeApprovalList } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { AGENT, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import ChangeListPage from './ChangeListPage';
import { makeApproval, makeChange, makeFreezeWindow } from './testUtils';

const CAB = { username: 'kurul1', roles: ['cab' as const] };
const MANAGER = { username: 'mudur', roles: ['change_manager' as const] };

function mockList(respond: (url: URL) => Change[] = () => []) {
  const calls: URL[] = [];
  server.use(
    http.get(apiUrl('/changes'), ({ request }) => {
      const url = new URL(request.url);
      calls.push(url);
      return HttpResponse.json(respond(url));
    }),
  );
  return calls;
}

const rows = () => screen.getAllByTestId('change-row');

describe('ChangeListPage — liste', () => {
  it('değişiklikleri Türkçe etiketlerle listeler; ayrıntı bağlantısı', async () => {
    const c = makeChange({
      number: 'CHG0000007',
      title: 'Çekirdek anahtar yazılımı',
      change_type: 'emergency',
      state: 'authorize',
      risk_level: 'high',
      risk_score: 68,
    });
    mockList(() => [c]);
    renderWithProviders(<ChangeListPage />, { user: AGENT, route: '/degisiklikler' });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Değişiklikler' }),
    ).toBeInTheDocument();
    const row = (await screen.findAllByTestId('change-row'))[0]!;
    expect(within(row).getByRole('link', { name: /Çekirdek anahtar yazılımı/ })).toHaveAttribute(
      'href',
      `/degisiklikler/${c.id}`,
    );
    expect(within(row).getByText('Acil')).toBeInTheDocument();
    expect(within(row).getByText('Yetkilendirme (CAB)')).toBeInTheDocument();
    expect(within(row).getByText(/Yüksek/)).toBeInTheDocument();
    expect(within(row).queryByText('emergency')).not.toBeInTheDocument();
  });

  it('durum, tür ve "benim taleplerim" sunucuya; risk istemcide süzülür', async () => {
    const low = makeChange({ title: 'Düşük riskli', risk_level: 'low' });
    const high = makeChange({ title: 'Yüksek riskli', risk_level: 'high' });
    const calls = mockList(() => [low, high]);
    const { user } = renderWithProviders(<ChangeListPage />, { route: '/degisiklikler' });
    await screen.findByText('Yüksek riskli');

    await user.selectOptions(screen.getByLabelText('Durum'), 'scheduled');
    await user.selectOptions(screen.getByLabelText('Tür'), 'normal');
    await user.click(screen.getByLabelText('Yalnızca benim taleplerim'));
    await waitFor(() => {
      const last = calls.at(-1)!;
      expect(last.searchParams.get('state')).toBe('scheduled');
      expect(last.searchParams.get('change_type')).toBe('normal');
      expect(last.searchParams.get('mine')).toBe('true');
    });

    await user.selectOptions(screen.getByLabelText('Risk'), 'high');
    await waitFor(() => {
      expect(rows()).toHaveLength(1);
    });
    expect(screen.queryByText('Düşük riskli')).not.toBeInTheDocument();
  });

  it('agent yeni değişiklik açabilir; CAB üyesi açamaz ama "oyumu bekleyenler" süzgecini görür', async () => {
    mockList();
    const { unmount } = renderWithProviders(<ChangeListPage />, { user: AGENT });
    expect(await screen.findByRole('link', { name: 'Yeni değişiklik' })).toHaveAttribute(
      'href',
      '/degisiklikler/yeni',
    );
    expect(screen.queryByLabelText('CAB oyumu bekleyenler')).not.toBeInTheDocument();
    unmount();

    renderWithProviders(<ChangeListPage />, { user: CAB });
    expect(await screen.findByLabelText('CAB oyumu bekleyenler')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Yeni değişiklik' })).not.toBeInTheDocument();
  });

  it('"CAB oyumu bekleyenler": yetkilendirmedeki, oy vermediğim ve talep etmediğim değişiklikler', async () => {
    const pending = makeChange({ title: 'Oy bekliyor', state: 'authorize' });
    const voted = makeChange({ title: 'Oy verdim', state: 'authorize' });
    const mine = makeChange({ title: 'Benim talebim', state: 'authorize', requested_by: 'kurul1' });
    const calls = mockList((url) =>
      url.searchParams.get('state') === 'authorize' ? [pending, voted, mine] : [],
    );
    const approvals = (items: ReturnType<typeof makeApproval>[]): ChangeApprovalList => ({
      status: { required: 2, approvals: items.length, rejections: 0, satisfied: false },
      items,
    });
    server.use(
      http.get(apiUrl(`/changes/${pending.id}/approvals`), () =>
        HttpResponse.json(approvals([makeApproval({ approver: 'baska' })])),
      ),
      http.get(apiUrl(`/changes/${voted.id}/approvals`), () =>
        HttpResponse.json(approvals([makeApproval({ approver: 'kurul1' })])),
      ),
      http.get(apiUrl(`/changes/${mine.id}/approvals`), () => HttpResponse.json(approvals([]))),
    );
    const { user } = renderWithProviders(<ChangeListPage />, { user: CAB });
    await user.click(await screen.findByLabelText('CAB oyumu bekleyenler'));
    await waitFor(() => {
      expect(calls.at(-1)!.searchParams.get('state')).toBe('authorize');
    });
    expect(await screen.findByText('Oy bekliyor')).toBeInTheDocument();
    await waitFor(() => {
      expect(rows()).toHaveLength(1);
    });
    expect(screen.queryByText('Oy verdim')).not.toBeInTheDocument();
    expect(screen.queryByText('Benim talebim')).not.toBeInTheDocument();
    // Durum süzgeci bu kipte kilitli
    expect(screen.getByLabelText('Durum')).toBeDisabled();
  });

  it('boş liste', async () => {
    mockList();
    renderWithProviders(<ChangeListPage />, { user: MANAGER });
    expect(await screen.findByText('Değişiklik bulunamadı')).toBeInTheDocument();
  });
});

describe('ChangeListPage — takvim', () => {
  function mockCalendar(changes: Change[], freezes = [makeFreezeWindow()]) {
    const calls: URL[] = [];
    server.use(
      http.get(apiUrl('/changes/calendar'), ({ request }) => {
        calls.push(new URL(request.url));
        return HttpResponse.json({ changes, freeze_windows: freezes });
      }),
    );
    return calls;
  }

  it('hafta görünümü: dondurma günleri metinle işaretli, çakışmalar bayraklı', async () => {
    mockList();
    const freeze = makeFreezeWindow({
      start_at: '2026-10-20T00:00:00+03:00',
      end_at: '2026-10-21T00:00:00+03:00',
      reason: 'Ay sonu kapanışı',
    });
    const inFreeze = makeChange({
      number: 'CHG-F',
      title: 'Dondurmada yama',
      state: 'scheduled',
      planned_start: '2026-10-20T10:00:00+03:00',
      planned_end: '2026-10-20T12:00:00+03:00',
    });
    const clean = makeChange({
      number: 'CHG-C',
      title: 'Temiz değişiklik',
      state: 'scheduled',
      planned_start: '2026-10-22T10:00:00+03:00',
      planned_end: '2026-10-22T12:00:00+03:00',
    });
    const calls = mockCalendar([inFreeze, clean], [freeze]);
    renderWithProviders(<ChangeListPage />, {
      route: '/degisiklikler?gorunum=takvim&takvim=hafta&tarih=2026-10-20',
    });

    const table = await screen.findByRole('table', { name: /Değişiklik takvimi/ });
    expect(calls[0]!.searchParams.get('from')).toBe(new Date(2026, 9, 19).toISOString());
    expect(calls[0]!.searchParams.get('to')).toBe(new Date(2026, 9, 26).toISOString());
    const frozenDay = await within(table).findByTestId('day-2026-10-20');
    expect(frozenDay).toHaveAttribute('data-freeze', 'true');
    expect(within(frozenDay).getByText(/Dondurma: Ay sonu kapanışı/)).toBeInTheDocument();
    const flagged = within(frozenDay).getByRole('link', { name: /Dondurmada yama/ });
    expect(flagged).toHaveAccessibleName(/Çakışma/);
    expect(within(frozenDay).getByText(/Çakışma/)).toBeInTheDocument();
    const cleanDay = within(table).getByTestId('day-2026-10-22');
    expect(cleanDay).toHaveAttribute('data-freeze', 'false');
    expect(
      within(cleanDay).getByRole('link', { name: /Temiz değişiklik/ }),
    ).not.toHaveAccessibleName(/Çakışma/);
  });

  it('ay görünümü ve ileri gezinme', async () => {
    mockList();
    const calls = mockCalendar([]);
    const { user } = renderWithProviders(<ChangeListPage />, {
      route: '/degisiklikler?gorunum=takvim&takvim=ay&tarih=2026-10-15',
    });
    expect(await screen.findByRole('heading', { name: /Ekim 2026/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Sonraki' }));
    expect(await screen.findByRole('heading', { name: /Kasım 2026/ })).toBeInTheDocument();
    await waitFor(() => {
      expect(calls.at(-1)!.searchParams.get('from')).toBe(new Date(2026, 9, 26).toISOString());
    });
    await user.click(screen.getByRole('radio', { name: 'Hafta' }));
    await waitFor(() => {
      expect(screen.getAllByRole('columnheader')).toHaveLength(7);
    });
  });

  it('liste/takvim geçişi', async () => {
    mockList();
    mockCalendar([]);
    const { user } = renderWithProviders(<ChangeListPage />, { route: '/degisiklikler' });
    await user.click(await screen.findByRole('radio', { name: 'Takvim' }));
    expect(await screen.findByRole('table', { name: /Değişiklik takvimi/ })).toBeInTheDocument();
  });
});
