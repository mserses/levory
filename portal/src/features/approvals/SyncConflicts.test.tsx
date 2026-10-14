import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { SyncEvent } from '@/api/types';
import { apiUrl } from '@/test/handlers';
import { APPROVER, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import ApprovalsPage from './ApprovalsPage';
import { makeConflict } from './testUtils';

function mockConflicts(respond: () => SyncEvent[] | Response) {
  let count = 0;
  server.use(
    http.get(apiUrl('/integrations/conflicts'), () => {
      count += 1;
      const r = respond();
      return r instanceof Response ? r : HttpResponse.json(r);
    }),
  );
  return () => count;
}

function mockResolve(respond: () => Response) {
  const calls: { id: string; winner: string | null }[] = [];
  server.use(
    http.post(apiUrl('/integrations/conflicts/:id/resolve'), ({ params, request }) => {
      calls.push({
        id: String(params.id),
        winner: new URL(request.url).searchParams.get('winner'),
      });
      return respond();
    }),
  );
  return calls;
}

const renderTab = () =>
  renderWithProviders(<ApprovalsPage />, { route: '/onaylar?sekme=cakisma', user: APPROVER });

type User = ReturnType<typeof renderTab>['user'];

const confirmIn = async (user: User, name: string) => {
  const dialog = await screen.findByRole('alertdialog');
  await user.click(within(dialog).getByRole('button', { name }));
  return dialog;
};

describe('ApprovalsPage — senkron çakışmaları', () => {
  it('yerel ve uzak alanları yan yana gösterir, farkları işaretler', async () => {
    mockConflicts(() => [makeConflict()]);
    renderTab();

    expect(screen.getByRole('tab', { name: 'Senkron çakışmaları' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const item = await screen.findByTestId('conflict-item');
    const c = within(item);
    expect(c.getByRole('heading', { level: 3 })).toHaveTextContent(
      'Yazıcı çalışmıyor · ServiceNow',
    );
    expect(item).toHaveTextContent('Uzak kayıt INC0010042');
    expect(c.getByText('4 alan farklı')).toBeInTheDocument();

    const table = c.getByRole('table', { name: 'Alan karşılaştırması' });
    expect(within(table).getByRole('columnheader', { name: 'Yerel (ServisZeka)' })).toBeVisible();
    expect(within(table).getByRole('columnheader', { name: 'Uzak (ServiceNow)' })).toBeVisible();

    const status = within(table).getByRole('row', { name: /Durum/ });
    expect(status).toHaveAttribute('data-differs', 'true');
    expect(status).toHaveTextContent(/Farklı.*Çalışılıyor.*Çözüldü/);
    expect(within(table).getByRole('row', { name: /Öncelik/ })).toHaveTextContent(
      /P3 · Orta.*P2 · Yüksek/,
    );
    const title = within(table).getByRole('row', { name: /Başlık/ });
    expect(title).not.toHaveAttribute('data-differs');
    // Uzakta olmayan alan "Alan yok" olarak gösterilir, fark sayılmaz
    expect(within(table).getByRole('row', { name: /Çözüm/ })).toHaveTextContent('Alan yok');

    expect(c.getByRole('link', { name: 'Kaydı incele' })).toHaveAttribute(
      'href',
      '/kayitlar/inc-42',
    );
  });

  it('yerel kaydı koru: onay sonrası winner=local gönderilir ve liste yenilenir', async () => {
    let resolved = false;
    mockConflicts(() => (resolved ? [] : [makeConflict()]));
    const calls = mockResolve(() => {
      resolved = true;
      return HttpResponse.json({ ...makeConflict(), status: 'resolved' });
    });
    const { user } = renderTab();

    const item = await screen.findByTestId('conflict-item');
    await user.click(within(item).getByRole('button', { name: 'Yerel kaydı koru' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Yerel kayıt korunsun mu?' });
    expect(dialog).toHaveTextContent(/Durum:.*Çözüldü → Çalışılıyor/);
    await confirmIn(user, 'Yerel kaydı koru');

    expect(await screen.findByText('Çakışma çözüldü')).toBeInTheDocument();
    expect(calls).toEqual([{ id: 'ev-1', winner: 'local' }]);
    expect(await screen.findByText('Açık senkron çakışması yok')).toBeInTheDocument();
  });

  it('uzak kaydı al: winner=remote; 502 bağlayıcı hatasını açıklar', async () => {
    mockConflicts(() => [makeConflict()]);
    const calls = mockResolve(() =>
      HttpResponse.json({ detail: 'ServiceNow zaman aşımı' }, { status: 502 }),
    );
    const { user } = renderTab();

    const item = await screen.findByTestId('conflict-item');
    await user.click(within(item).getByRole('button', { name: 'Uzak kaydı al' }));
    await screen.findByRole('alertdialog', { name: 'Uzak kayıt alınsın mı?' });
    await confirmIn(user, 'Uzak kaydı al');

    const alert = await within(item).findByRole('alert');
    expect(alert).toHaveTextContent(/ServiceNow sistemine ulaşılamadı: ServiceNow zaman aşımı/);
    expect(calls).toEqual([{ id: 'ev-1', winner: 'remote' }]);
  });

  it('404: çakışma zaten çözülmüşse bildirir ve listeyi yeniler', async () => {
    const count = mockConflicts(() => [makeConflict()]);
    mockResolve(() =>
      HttpResponse.json({ detail: 'Açık çakışma kaydı bulunamadı' }, { status: 404 }),
    );
    const { user } = renderTab();

    const item = await screen.findByTestId('conflict-item');
    await user.click(within(item).getByRole('button', { name: 'Yerel kaydı koru' }));
    await confirmIn(user, 'Yerel kaydı koru');

    expect(await screen.findByText('Çakışma zaten çözülmüş')).toBeInTheDocument();
    await waitFor(() => {
      expect(count()).toBeGreaterThan(1);
    });
  });

  it('403 çözüm: yetki hatası', async () => {
    mockConflicts(() => [makeConflict()]);
    mockResolve(() => HttpResponse.json({ detail: 'Yetki yok' }, { status: 403 }));
    const { user } = renderTab();

    const item = await screen.findByTestId('conflict-item');
    await user.click(within(item).getByRole('button', { name: 'Uzak kaydı al' }));
    await confirmIn(user, 'Uzak kaydı al');
    expect(await within(item).findByRole('alert')).toHaveTextContent(
      'Bu çakışmayı çözme yetkiniz yok.',
    );
  });

  it('liste 403 ve boş durum', async () => {
    mockConflicts(() => HttpResponse.json({ detail: 'Yetki yok' }, { status: 403 }));
    renderTab();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Senkron çakışmalarına erişiminiz yok',
    );
  });

  it('çakışma yoksa boş durum', async () => {
    mockConflicts(() => []);
    renderTab();
    expect(await screen.findByText('Açık senkron çakışması yok')).toBeInTheDocument();
  });

  it('bozuk yükte çökmez', async () => {
    mockConflicts(() => [makeConflict({ payload: { incident_id: 5 } })]);
    renderTab();
    const item = await screen.findByTestId('conflict-item');
    expect(item).toHaveTextContent('Çakışma kaydında alan ayrıntısı yok.');
    expect(within(item).queryByRole('link', { name: 'Kaydı incele' })).toBeNull();
  });
});
