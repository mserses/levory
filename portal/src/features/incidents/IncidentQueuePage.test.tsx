import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { Incident } from '@/api/types';
import { makeIncident } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { AGENT, APPROVER, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import IncidentQueuePage from './IncidentQueuePage';
import { PAGE_SIZE } from './queueParams';
import { fromNow } from './testUtils';

/** /incidents isteklerini kaydeder; sorguya göre yanıt üretir */
function mockList(respond: (url: URL) => Incident[] | Response = () => []) {
  const calls: URL[] = [];
  server.use(
    http.get(apiUrl('/incidents'), ({ request }) => {
      const url = new URL(request.url);
      calls.push(url);
      const result = respond(url);
      return result instanceof Response ? result : HttpResponse.json(result);
    }),
  );
  return calls;
}

const rowNumbers = () =>
  screen.getAllByTestId('incident-row').map((r) => within(r).getByText(/^INC/).textContent);

describe('IncidentQueuePage', () => {
  it('kuyruğu listeler: öncelik, durum, SLA rozetleri ve ayrıntı bağlantısı', async () => {
    const created = fromNow(-120);
    const breached = makeIncident({
      number: 'INC-BREACH',
      title: 'E-posta gönderilemiyor',
      priority: 'P2',
      status: 'in_progress',
      assignee: 'ayse',
      assignment_group: 'collaboration',
      created_at: created,
      responded_at: created,
      resolve_due_at: fromNow(-10),
    });
    const atRisk = makeIncident({
      number: 'INC-RISK',
      created_at: created,
      responded_at: created,
      resolve_due_at: fromNow(20), // %85 geçti
    });
    const ok = makeIncident({
      number: 'INC-OK',
      created_at: created,
      responded_at: created,
      resolve_due_at: fromNow(60 * 24),
    });
    const paused = makeIncident({
      number: 'INC-PAUSED',
      status: 'on_hold',
      on_hold_reason: 'awaiting_vendor',
      created_at: created,
      responded_at: created,
      resolve_due_at: fromNow(5),
      sla_paused_at: fromNow(-30),
    });
    const calls = mockList(() => [ok, paused, atRisk, breached]);
    renderWithProviders(<IncidentQueuePage />, { route: '/kayitlar' });

    expect(await screen.findByRole('heading', { level: 1, name: 'Kayıtlar' })).toBeInTheDocument();
    await screen.findByText('INC-OK');
    // Varsayılan sıralama: SLA vadesi (ihlal → en yakın → ileri → duraklatılmış)
    expect(rowNumbers()).toEqual(['INC-BREACH', 'INC-RISK', 'INC-OK', 'INC-PAUSED']);

    const sla = (n: string) =>
      screen
        .getByText(n)
        .closest('tr')!
        .querySelector('[data-sla-state]')
        ?.getAttribute('data-sla-state');
    expect(sla('INC-BREACH')).toBe('breached');
    expect(sla('INC-RISK')).toBe('at_risk');
    expect(sla('INC-OK')).toBe('on_track');
    expect(sla('INC-PAUSED')).toBe('paused');

    const row = screen.getByText('INC-BREACH').closest('tr')!;
    expect(within(row).getByText('P2 · Yüksek')).toBeInTheDocument();
    expect(within(row).getByText('Çalışılıyor')).toBeInTheDocument();
    expect(within(row).getByText('ayse')).toBeInTheDocument();
    // Atama grubu ham anahtar (collaboration) değil Türkçe adıyla
    expect(within(row).getByText(/E-posta ve iş birliği ekibi/)).toBeInTheDocument();
    expect(within(row).queryByText(/collaboration/)).not.toBeInTheDocument();
    expect(within(row).getByRole('link')).toHaveAttribute('href', `/kayitlar/${breached.id}`);
    expect(screen.getAllByText('Atanmadı')).toHaveLength(3);

    expect(calls[0]?.searchParams.get('limit')).toBe(String(PAGE_SIZE + 1));
    expect(calls[0]?.searchParams.get('offset')).toBe('0');
  });

  it('"En yeni" sıralaması sunucu sırasını korur', async () => {
    mockList(() => [
      makeIncident({ number: 'INC-A', responded_at: fromNow(-1), resolve_due_at: fromNow(600) }),
      makeIncident({ number: 'INC-B', responded_at: fromNow(-1), resolve_due_at: fromNow(-5) }),
    ]);
    const { user } = renderWithProviders(<IncidentQueuePage />, { route: '/kayitlar' });
    await screen.findByText('INC-A');
    expect(rowNumbers()).toEqual(['INC-B', 'INC-A']);
    await user.selectOptions(screen.getByLabelText('Sıralama'), 'newest');
    await waitFor(() => {
      expect(rowNumbers()).toEqual(['INC-A', 'INC-B']);
    });
  });

  it('filtreler API sorgusuna yansır: durum, öncelik, atanan, mine; temizleme', async () => {
    const calls = mockList(() => [makeIncident()]);
    const { user } = renderWithProviders(<IncidentQueuePage />, { route: '/kayitlar' });
    await screen.findAllByTestId('incident-row');

    await user.selectOptions(screen.getByLabelText('Durum'), 'on_hold');
    await waitFor(() => {
      expect(calls.at(-1)?.searchParams.get('status')).toBe('on_hold');
    });
    await user.selectOptions(screen.getByLabelText('Öncelik'), 'P1');
    await waitFor(() => {
      expect(calls.at(-1)?.searchParams.get('priority')).toBe('P1');
    });
    await user.type(screen.getByLabelText('Atanan'), 'mehmet{Enter}');
    await waitFor(() => {
      expect(calls.at(-1)?.searchParams.get('assignee')).toBe('mehmet');
    });
    await user.click(screen.getByRole('checkbox', { name: /benim açtığım/ }));
    await waitFor(() => {
      expect(calls.at(-1)?.searchParams.get('mine')).toBe('true');
    });
    const last = calls.at(-1)!;
    expect(last.searchParams.get('status')).toBe('on_hold');
    expect(last.searchParams.get('priority')).toBe('P1');

    await user.click(screen.getByRole('button', { name: 'Bana atananlar' }));
    await waitFor(() => {
      expect(calls.at(-1)?.searchParams.get('assignee')).toBe(AGENT.username);
    });
    expect(screen.getByRole('button', { name: 'Bana atananlar' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await user.click(screen.getByRole('button', { name: 'Filtreleri temizle' }));
    await waitFor(() => {
      const q = calls.at(-1)!.searchParams;
      expect([q.get('status'), q.get('priority'), q.get('assignee'), q.get('mine')]).toEqual([
        null,
        null,
        null,
        null,
      ]);
    });
    expect(screen.getByLabelText('Atanan')).toHaveValue('');
  });

  it('URL filtreleri ilk yüklemede uygulanır', async () => {
    const calls = mockList(() => []);
    renderWithProviders(<IncidentQueuePage />, {
      route: '/kayitlar?durum=new&oncelik=P3&benim=1&sayfa=2',
      path: '/kayitlar',
    });
    await screen.findByText('Bu sayfada kayıt yok');
    const q = calls[0]!.searchParams;
    expect(q.get('status')).toBe('new');
    expect(q.get('priority')).toBe('P3');
    expect(q.get('mine')).toBe('true');
    expect(q.get('offset')).toBe(String(PAGE_SIZE));
    expect(screen.getByLabelText('Durum')).toHaveValue('new');
    expect(screen.getByRole('checkbox', { name: /benim açtığım/ })).toBeChecked();
  });

  it('sayfalama: fazladan kayıt varsa sonraki sayfa açılır', async () => {
    const page1 = Array.from({ length: PAGE_SIZE + 1 }, (_, i) =>
      makeIncident({ number: `INC-P1-${String(i)}` }),
    );
    const page2 = [makeIncident({ number: 'INC-P2-0' })];
    const calls = mockList((url) => (url.searchParams.get('offset') === '0' ? page1 : page2));
    const { user } = renderWithProviders(<IncidentQueuePage />, { route: '/kayitlar' });

    await screen.findByText('INC-P1-0');
    expect(screen.getAllByTestId('incident-row')).toHaveLength(PAGE_SIZE);
    expect(screen.queryByText(`INC-P1-${String(PAGE_SIZE)}`)).not.toBeInTheDocument();
    const pager = screen.getByRole('navigation', { name: 'Sayfalama' });
    expect(within(pager).getByRole('button', { name: /Önceki/ })).toBeDisabled();

    await user.click(within(pager).getByRole('button', { name: /Sonraki/ }));
    await screen.findByText('INC-P2-0');
    expect(calls.at(-1)?.searchParams.get('offset')).toBe(String(PAGE_SIZE));
    expect(within(pager).getByRole('button', { name: /Sonraki/ })).toBeDisabled();
    await user.click(within(pager).getByRole('button', { name: /Önceki/ }));
    await screen.findByText('INC-P1-0');
  });

  it('boş kuyruk ve filtreli boş sonuç', async () => {
    mockList(() => []);
    renderWithProviders(<IncidentQueuePage />, { route: '/kayitlar' });
    expect(await screen.findByText('Kayıt bulunamadı')).toBeInTheDocument();
    expect(screen.getByText('Kuyruk şu an boş.')).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Sayfalama' })).not.toBeInTheDocument();
  });

  it('sunucu hatası: hata durumu ve yeniden deneme', async () => {
    let fail = true;
    mockList(() =>
      fail
        ? HttpResponse.json({ detail: 'Veritabanına ulaşılamıyor' }, { status: 503 })
        : [makeIncident({ number: 'INC-BACK' })],
    );
    const { user } = renderWithProviders(<IncidentQueuePage />, { route: '/kayitlar' });
    expect(await screen.findByRole('alert')).toHaveTextContent('Veritabanına ulaşılamıyor');
    fail = false;
    await user.click(screen.getByRole('button', { name: 'Yeniden dene' }));
    expect(await screen.findByText('INC-BACK')).toBeInTheDocument();
  });

  it('rota koruması: approver kuyruğu görür', async () => {
    mockList(() => [makeIncident({ number: 'INC-APP' })]);
    renderApp({ route: '/kayitlar', user: APPROVER });
    expect(await screen.findByText('INC-APP')).toBeInTheDocument();
  });

  it('requester kuyruğa erişemez', async () => {
    renderApp({ route: '/kayitlar', user: REQUESTER });
    expect(await screen.findByText('Bu sayfayı görüntüleme yetkiniz yok')).toBeInTheDocument();
    expect(screen.queryByTestId('incident-row')).not.toBeInTheDocument();
  });
});
