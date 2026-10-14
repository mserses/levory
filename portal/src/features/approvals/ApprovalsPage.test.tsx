import { screen, waitFor, within } from '@testing-library/react';
import { HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { DecisionInboxItem } from '@/api/types';
import { makeInboxItem } from '@/test/fixtures';
import { AGENT, APPROVER, ADMIN, renderApp, renderWithProviders } from '@/test/render';
import ApprovalsPage from './ApprovalsPage';
import { mockApprove, mockInbox, urgentRaiseItem } from './testUtils';

const renderPage = (route = '/onaylar', user = APPROVER) =>
  renderWithProviders(<ApprovalsPage />, { route, user });

const card = async (number: string) => {
  const heading = await screen.findByRole('heading', { level: 3, name: new RegExp(number) });
  const item = heading.closest('li');
  if (!item) throw new Error('kart bulunamadı');
  return item;
};

describe('ApprovalsPage — onay kutusu', () => {
  it('onay bekleyenleri listeler: kayıt, mevcut ↔ önerilen, P1 vurgusu, güven, gerekçe, kaynak', async () => {
    const { item, incident } = urgentRaiseItem();
    const calls = mockInbox({ needs_approval: [item] }, [incident]);
    renderPage();

    expect(await screen.findByRole('heading', { level: 1, name: 'Onaylar' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Onay bekleyen' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const el = await card('INC0000101');
    expect(calls.outcomes[0]).toEqual(['needs_approval']);
    expect(calls.limits[0]).toBe('20');

    const c = within(el);
    expect(c.getByRole('link', { name: /INC0000101 Muhasebe sunucusu/ })).toHaveAttribute(
      'href',
      `/kayitlar/${incident.id}`,
    );
    // P1/P2 yükseltme vurgusu
    expect(c.getByText('Kritik yükseltme')).toBeInTheDocument();
    expect(el.querySelector('[data-priority-change="urgent_raise"]')).not.toBeNull();

    // Mevcut değerler kayıttan yüklenir
    const table = c.getByRole('table', { name: /Önerilen değişiklikler/ });
    await waitFor(() => {
      expect(within(table).getByRole('row', { name: /Etki/ })).toHaveTextContent(
        /Düşük \(tek kullanıcı\).*Yüksek \(kurum geneli/,
      );
    });
    expect(within(table).getByRole('row', { name: /Kategori/ })).toHaveTextContent('Aynı kalır');
    expect(within(table).getByRole('row', { name: /Öncelik/ })).toHaveTextContent(
      /P4 · Düşük.*P1 · Kritik.*Değişecek/,
    );

    // Yapay zekâ kararı: sonuç, güven, gerekçe (düz metin), kaynak
    expect(c.getByText('Onay bekliyor')).toBeInTheDocument();
    expect(c.getByRole('meter', { name: 'Güven' })).toHaveAttribute('aria-valuenow', '91');
    expect(c.getByText(/Tüm muhasebe birimi etkileniyor/)).toHaveTextContent(
      '<script>alert(1)</script>',
    );
    expect(el.querySelector('script')).toBeNull();
    expect(c.getByRole('link', { name: 'KB0000007' })).toHaveAttribute(
      'href',
      '/bilgi-bankasi/KB0000007',
    );
    expect(c.getByText(/P4 -> P1 yükseltmesi otomatik uygulanamaz/)).toBeInTheDocument();
  });

  it('onayla: onay penceresi değişiklikleri gösterir, onaylayınca liste yenilenir', async () => {
    const { item, incident } = urgentRaiseItem();
    let approved = false;
    mockInbox({ needs_approval: () => (approved ? [] : [item]) }, [incident]);
    const approveCalls = mockApprove(() => {
      approved = true;
      return HttpResponse.json({ ...item, approved_by: 'sef.ali', approved_at: item.created_at });
    });
    const { user } = renderPage();

    const el = await card('INC0000101');
    await within(el).findByText(/Düşük \(tek kullanıcı\)/);
    await user.click(within(el).getByRole('button', { name: 'Onayla ve uygula' }));

    const dialog = await screen.findByRole('alertdialog', { name: 'Öneri onaylansın mı?' });
    expect(dialog).toHaveTextContent(/Etki:.*Düşük.*→.*Yüksek/);
    expect(dialog).toHaveTextContent('Bu onay önceliği P1 seviyesine yükseltir.');
    // Vazgeç varsayılan odak (yanlışlıkla onay olmasın)
    expect(within(dialog).getByRole('button', { name: 'Vazgeç' })).toHaveFocus();

    await user.click(within(dialog).getByRole('button', { name: 'Onayla ve uygula' }));

    expect(await screen.findByText('Öneri onaylandı')).toBeInTheDocument();
    expect(approveCalls).toEqual([item.id]);
    expect(await screen.findByText('Onay bekleyen öneri yok')).toBeInTheDocument();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('vazgeç: istek gönderilmez', async () => {
    const { item, incident } = urgentRaiseItem();
    mockInbox({ needs_approval: [item] }, [incident]);
    const approveCalls = mockApprove(() => HttpResponse.json({}));
    const { user } = renderPage();

    const el = await card('INC0000101');
    await user.click(within(el).getByRole('button', { name: 'Onayla ve uygula' }));
    await user.click(await screen.findByRole('button', { name: 'Vazgeç' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(approveCalls).toEqual([]);
  });

  it('409 bayat öneri: yeniden triage gerektiğini açıklar ve kayda bağlantı verir', async () => {
    const { item, incident } = urgentRaiseItem();
    const calls = mockInbox({ needs_approval: [item] }, [incident]);
    mockApprove(() =>
      HttpResponse.json(
        { detail: 'Kayıt öneriden sonra değişti; yeniden triage gerekli' },
        { status: 409 },
      ),
    );
    const { user } = renderPage();

    const el = await card('INC0000101');
    await user.click(within(el).getByRole('button', { name: 'Onayla ve uygula' }));
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Onayla ve uygula',
      }),
    );

    const alert = await within(el).findByRole('alert');
    expect(alert).toHaveTextContent('Kayıt öneriden sonra değişti; yeniden triage gerekli.');
    expect(alert).toHaveTextContent(/üzerine yazılmaması için bu öneri uygulanmadı/);
    expect(
      within(alert).getByRole('link', { name: 'Kayda git ve yeniden triage başlat' }),
    ).toHaveAttribute('href', `/kayitlar/${incident.id}`);
    // Bayat öneri yeniden onaylanamaz; liste tazelendi
    expect(within(el).getByRole('button', { name: 'Onayla ve uygula' })).toBeDisabled();
    await waitFor(() => {
      expect(calls.outcomes.length).toBeGreaterThan(1);
    });
  });

  it('sürümler farklıysa onaydan önce uyarır', async () => {
    const { item, incident } = urgentRaiseItem({
      incident_version: 3,
      incident_current_version: 5,
    });
    mockInbox({ needs_approval: [item] }, [incident]);
    renderPage();

    const el = await card('INC0000101');
    expect(within(el).getByTestId('stale-hint')).toHaveTextContent(
      /Kayıt bu öneriden sonra değişti \(sürüm 3 → 5\)/,
    );
  });

  it('403 onay: yetki hatasını kart içinde gösterir', async () => {
    const { item, incident } = urgentRaiseItem();
    mockInbox({ needs_approval: [item] }, [incident]);
    mockApprove(() => HttpResponse.json({ detail: 'Yetki yok' }, { status: 403 }));
    const { user } = renderPage();

    const el = await card('INC0000101');
    await user.click(within(el).getByRole('button', { name: 'Onayla ve uygula' }));
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Onayla ve uygula',
      }),
    );
    const alert = await within(el).findByRole('alert');
    expect(alert).toHaveTextContent('Bu öneriyi onaylama yetkiniz yok.');
    expect(within(el).getByRole('button', { name: 'Onayla ve uygula' })).toBeDisabled();
  });

  it('409 (zaten onaylanmış): sunucu mesajını gösterir', async () => {
    const { item, incident } = urgentRaiseItem();
    mockInbox({ needs_approval: [item] }, [incident]);
    mockApprove(() =>
      HttpResponse.json({ detail: 'Karar zaten mehmet tarafından onaylanmış.' }, { status: 409 }),
    );
    const { user } = renderPage();

    const el = await card('INC0000101');
    await user.click(within(el).getByRole('button', { name: 'Onayla ve uygula' }));
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', {
        name: 'Onayla ve uygula',
      }),
    );
    expect(await within(el).findByRole('alert')).toHaveTextContent(
      'Karar zaten mehmet tarafından onaylanmış.',
    );
  });

  it('P1’den düşürme önerisini ayrıca vurgular; kayıt yüklenemezse mevcut değerler bilinmiyor', async () => {
    const item = makeInboxItem({
      incident_number: 'INC0000202',
      incident_priority: 'P1',
      outcome: 'needs_approval',
      output: {
        proposed: { impact: 'low' },
        current_priority: 'P1',
        proposed_priority: 'P3',
      },
    });
    mockInbox({ needs_approval: [item] }); // GET /incidents/{id} → 404
    renderPage();

    const el = await card('INC0000202');
    expect(within(el).getByText('P1/P2’den düşürme')).toBeInTheDocument();
    expect(await within(el).findByText(/Kaydın mevcut değerleri alınamadı/)).toBeInTheDocument();
    expect(within(el).getByRole('row', { name: /Etki/ })).toHaveTextContent('Bilinmiyor');
  });

  it('sekmeler: öneriler ve yönlendirilenler ayrı sorgular; yönlendirilen onaylanamaz', async () => {
    const suggested = makeInboxItem({ incident_number: 'INC-SUG', outcome: 'suggested' });
    const escalated = makeInboxItem({
      incident_number: 'INC-ESC',
      outcome: 'escalated',
      confidence: 0.4,
    });
    const calls = mockInbox({ suggested: [suggested], escalated: [escalated] });
    const { user } = renderPage();

    expect(await screen.findByText('Onay bekleyen öneri yok')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Öneriler' }));
    const sugCard = await card('INC-SUG');
    expect(within(sugCard).getByRole('button', { name: 'Onayla ve uygula' })).toBeEnabled();
    expect(within(sugCard).getByText('Öneri')).toBeInTheDocument();

    // Klavye: sağ ok bir sonraki sekmeye geçer ve odaklar
    screen.getByRole('tab', { name: 'Öneriler' }).focus();
    await user.keyboard('{ArrowRight}');
    const escTab = screen.getByRole('tab', { name: 'İnsana yönlendirilen' });
    expect(escTab).toHaveFocus();
    expect(escTab).toHaveAttribute('aria-selected', 'true');

    const escCard = await card('INC-ESC');
    expect(within(escCard).queryByRole('button', { name: 'Onayla ve uygula' })).toBeNull();
    expect(escCard).toHaveTextContent(/doğrudan onaylanamaz/);
    expect(within(escCard).getByText('İnsana yönlendirildi')).toBeInTheDocument();

    expect(calls.outcomes).toEqual(
      expect.arrayContaining([['needs_approval'], ['suggested'], ['escalated']]),
    );
  });

  it('?sekme= adresinden sekmeyi açar', async () => {
    mockInbox({ escalated: [] });
    renderPage('/onaylar?sekme=yonlendirilen');
    expect(await screen.findByText('İnsana yönlendirilen karar yok')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'İnsana yönlendirilen' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('daha fazla göster: limit büyür', async () => {
    const items: DecisionInboxItem[] = Array.from({ length: 20 }, (_, i) =>
      makeInboxItem({ incident_number: `INC-${String(i)}`, outcome: 'needs_approval' }),
    );
    const calls = mockInbox({ needs_approval: items });
    const { user } = renderPage();

    await card('INC-0');
    expect(screen.getAllByTestId('decision-item')).toHaveLength(20);
    await user.click(screen.getByRole('button', { name: 'Daha fazla göster' }));
    await waitFor(() => {
      expect(calls.limits).toContain('40');
    });
  });

  it('liste 403: erişim açıklaması gösterir', async () => {
    mockInbox({
      needs_approval: () => HttpResponse.json({ detail: 'Yetki yok' }, { status: 403 }),
    });
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Onay kutusuna erişiminiz yok.*yalnızca onaylayıcı ve yönetici/,
    );
  });

  it('liste 500: yeniden dene ile toparlanır', async () => {
    let fail = true;
    mockInbox({
      needs_approval: () =>
        fail ? HttpResponse.json({ detail: 'Sunucu hatası' }, { status: 500 }) : [],
    });
    const { user } = renderPage();

    const alert = await screen.findByRole('alert', {}, { timeout: 4000 });
    expect(alert).toHaveTextContent('Sunucu hatası');
    fail = false;
    await user.click(within(alert).getByRole('button', { name: 'Yeniden dene' }));
    expect(await screen.findByText('Onay bekleyen öneri yok')).toBeInTheDocument();
  });
});

describe('ApprovalsPage — rota koruması', () => {
  it('agent rolü /onaylar sayfasını göremez', async () => {
    renderApp({ route: '/onaylar', user: AGENT });
    expect(
      await screen.findByRole('heading', { name: 'Bu sayfayı görüntüleme yetkiniz yok' }),
    ).toBeInTheDocument();
  });

  it('admin rolü tam uygulamada onay kutusunu açar', async () => {
    mockInbox({ needs_approval: [] });
    renderApp({ route: '/onaylar', user: ADMIN });
    expect(await screen.findByRole('heading', { level: 1, name: 'Onaylar' })).toBeInTheDocument();
    expect(await screen.findByText('Onay bekleyen öneri yok')).toBeInTheDocument();
  });
});
