import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeDecision, makeIncident } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { AGENT, APPROVER, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import IncidentDetailPage from './IncidentDetailPage';
import { fromNow, mockIncidentApi } from './testUtils';

function renderDetail(id: string, user = AGENT) {
  return renderWithProviders(<IncidentDetailPage />, {
    user,
    route: `/kayitlar/${id}`,
    path: '/kayitlar/:id',
  });
}

const baseIncident = () =>
  makeIncident({
    number: 'INC0000042',
    title: 'VPN bağlantısı kurulamıyor',
    description: 'Evden bağlanamıyorum.\nSertifika hatası alıyorum.',
    caller: 'zeynep',
    category: 'ag',
    service: 'vpn',
    assignment_group: 'ag-ekibi',
    assignee: 'ayse',
    status: 'in_progress',
    priority: 'P4',
    impact: 'low',
    urgency: 'medium',
    version: 3,
    created_at: fromNow(-60),
    responded_at: fromNow(-50),
    resolve_due_at: fromNow(60 * 24),
    ai_confidence: 0.82,
  });

describe('IncidentDetailPage — ayrıntı', () => {
  it('tüm alanları gösterir', async () => {
    const incident = baseIncident();
    mockIncidentApi(incident);
    renderDetail(incident.id);

    expect(
      await screen.findByRole('heading', { level: 1, name: 'VPN bağlantısı kurulamıyor' }),
    ).toBeInTheDocument();
    expect(screen.getByText('INC0000042')).toBeInTheDocument();
    const details = screen.getByRole('region', { name: 'Ayrıntılar' });
    const dd = (term: string) => within(details).getByText(term).nextElementSibling;
    expect(dd('Tür')).toHaveTextContent('Arıza / sorun');
    expect(dd('Arayan')).toHaveTextContent('zeynep');
    expect(dd('Kategori')).toHaveTextContent('Ağ ve bağlantı');
    expect(dd('Hizmet')).toHaveTextContent('Uzaktan erişim VPN');
    expect(dd('Atama grubu')).toHaveTextContent('Diğer (ag-ekibi)');
    expect(dd('Atanan')).toHaveTextContent('ayse');
    expect(dd('Etki')).toHaveTextContent(/^Düşük/);
    expect(dd('Aciliyet')).toHaveTextContent(/^Orta/);
    expect(dd('Sürüm')).toHaveTextContent('3');
    expect(within(details).getByText(/Sertifika hatası alıyorum/)).toHaveClass('prewrap');
    expect(within(details).getByRole('meter', { name: 'Yapay zekâ güveni' })).toHaveAttribute(
      'aria-valuenow',
      '82',
    );
    expect(details.querySelector('[data-sla-state="on_track"]')).not.toBeNull();
    expect(await screen.findByText('Henüz yapay zekâ kararı yok')).toBeInTheDocument();
  });

  it('kayıt yoksa 404 mesajı', async () => {
    server.use(
      http.get(apiUrl('/incidents/yok'), () =>
        HttpResponse.json({ detail: 'Olay bulunamadı' }, { status: 404 }),
      ),
    );
    renderDetail('yok');
    expect(await screen.findByRole('alert')).toHaveTextContent('Kayıt bulunamadı');
    expect(screen.getByRole('alert')).toHaveTextContent('Olay bulunamadı');
    expect(screen.getByRole('link', { name: /Kayıtlara dön/ })).toHaveAttribute(
      'href',
      '/kayitlar',
    );
  });

  it('approver salt okunur görür (düzenle/durum/triage yok)', async () => {
    const incident = baseIncident();
    mockIncidentApi(incident, [
      makeDecision({ incident_id: incident.id, outcome: 'needs_approval', incident_version: 3 }),
    ]);
    renderDetail(incident.id, APPROVER);
    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByRole('button', { name: 'Düzenle' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Yapay zekâ ile sınıflandır' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/yalnızca destek uzmanları/)).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Onay kutusunda aç' })).toHaveAttribute(
      'href',
      '/onaylar',
    );
  });
});

describe('IncidentDetailPage — düzenleme (optimistik kilit)', () => {
  it('yalnızca değişen alanları If-Match sürümüyle gönderir', async () => {
    const incident = baseIncident();
    const api = mockIncidentApi(incident);
    const { user } = renderDetail(incident.id);
    await user.click(await screen.findByRole('button', { name: 'Düzenle' }));

    const form = screen.getByRole('form', { name: 'Kaydı düzenle' });
    await user.clear(within(form).getByLabelText(/Başlık/));
    await user.type(within(form).getByLabelText(/Başlık/), 'VPN sertifika hatası');
    await user.clear(within(form).getByLabelText('Atanan'));
    await user.selectOptions(within(form).getByLabelText('Etki'), 'high');
    // Öncelik önizlemesi: high x medium = P2
    expect(within(form).getByText('P2 · Yüksek')).toBeInTheDocument();
    await user.click(within(form).getByRole('button', { name: 'Kaydet' }));

    expect(await screen.findByText('Kayıt güncellendi')).toBeInTheDocument();
    expect(api.patches).toEqual([
      {
        ifMatch: '"3"',
        body: { title: 'VPN sertifika hatası', impact: 'high', assignee: null },
      },
    ]);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'VPN sertifika hatası' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Kaydı düzenle' })).not.toBeInTheDocument();
  });

  it('istemci doğrulaması: boş başlık gönderilmez', async () => {
    const incident = baseIncident();
    const api = mockIncidentApi(incident);
    const { user } = renderDetail(incident.id);
    await user.click(await screen.findByRole('button', { name: 'Düzenle' }));
    await user.clear(screen.getByLabelText(/Başlık/));
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(screen.getByText('Başlık boş olamaz.')).toBeInTheDocument();
    expect(screen.getByLabelText(/Başlık/)).toHaveAttribute('aria-invalid', 'true');
    expect(api.patches).toHaveLength(0);
  });

  it('409: "kayıt başkası tarafından değişti" penceresi ve güncel kaydı yükleme', async () => {
    const incident = baseIncident();
    const api = mockIncidentApi(incident);
    const { user } = renderDetail(incident.id);
    await user.click(await screen.findByRole('button', { name: 'Düzenle' }));

    // Bu arada başka biri kaydı değiştirir
    api.incident = { ...api.incident, title: 'Başkasının başlığı', version: 4 };

    await user.selectOptions(screen.getByLabelText('Aciliyet'), 'high');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));

    const dialog = await screen.findByRole('alertdialog', {
      name: 'Kayıt başkası tarafından değişti',
    });
    expect(api.patches[0]?.ifMatch).toBe('"3"');
    expect(within(dialog).getByText('Aciliyet')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Güncel kaydı yükle' }));

    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
    // Form güncel kayıtla yeniden açılır; yeni kayıt sürüm 4 üzerinden gönderilir
    expect(screen.getByLabelText(/Başlık/)).toHaveValue('Başkasının başlığı');
    await user.selectOptions(screen.getByLabelText('Aciliyet'), 'high');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(await screen.findByText('Kayıt güncellendi')).toBeInTheDocument();
    expect(api.patches[1]).toEqual({ ifMatch: '"4"', body: { urgency: 'high' } });
  });

  it('422 doğrulama hatası formda gösterilir', async () => {
    const incident = baseIncident();
    mockIncidentApi(incident);
    server.use(
      http.patch(apiUrl(`/incidents/${incident.id}`), () =>
        HttpResponse.json(
          { detail: [{ loc: ['body', 'title'], msg: 'En fazla 200 karakter', type: 'x' }] },
          { status: 422 },
        ),
      ),
    );
    const { user } = renderDetail(incident.id);
    await user.click(await screen.findByRole('button', { name: 'Düzenle' }));
    await user.type(screen.getByLabelText(/Başlık/), '!');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(await screen.findByText('En fazla 200 karakter')).toBeInTheDocument();
    expect(screen.getByRole('form', { name: 'Kaydı düzenle' })).toBeInTheDocument();
  });
});

describe('IncidentDetailPage — durum geçişi', () => {
  it('beklemeye alma neden ister ve SLA etkisini gösterir', async () => {
    const incident = baseIncident();
    const api = mockIncidentApi(incident);
    const { user } = renderDetail(incident.id);
    const form = await screen.findByRole('form', { name: 'Durum değiştir' });

    // Yalnızca izin verilen geçişler (in_progress →)
    const options = within(within(form).getByLabelText(/Yeni durum/))
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(options).toEqual(['Seçiniz', 'Atandı', 'Beklemede', 'Çözüldü', 'İptal edildi']);

    await user.selectOptions(within(form).getByLabelText(/Yeni durum/), 'on_hold');
    await user.click(within(form).getByRole('button', { name: 'Durumu güncelle' }));
    expect(within(form).getByText('Beklemeye almak için neden seçin.')).toBeInTheDocument();
    expect(api.patches).toHaveLength(0);

    await user.selectOptions(within(form).getByLabelText(/Bekleme nedeni/), 'internal');
    expect(within(form).getByText('SLA işlemeye devam eder')).toBeInTheDocument();
    await user.selectOptions(within(form).getByLabelText(/Bekleme nedeni/), 'awaiting_vendor');
    expect(within(form).getByText('SLA duraklatılır')).toBeInTheDocument();

    await user.click(within(form).getByRole('button', { name: 'Durumu güncelle' }));
    expect(await screen.findByText('Kayıt güncellendi')).toBeInTheDocument();
    expect(api.patches[0]).toEqual({
      ifMatch: '"3"',
      body: { status: 'on_hold', on_hold_reason: 'awaiting_vendor' },
    });
  });

  it('çözüldü durumu çözüm metni ister', async () => {
    const incident = baseIncident();
    const api = mockIncidentApi(incident);
    const { user } = renderDetail(incident.id);
    const form = await screen.findByRole('form', { name: 'Durum değiştir' });
    await user.selectOptions(within(form).getByLabelText(/Yeni durum/), 'resolved');
    await user.click(within(form).getByRole('button', { name: 'Durumu güncelle' }));
    expect(
      within(form).getByText('Çözüldü durumu için çözüm açıklaması zorunludur.'),
    ).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/Çözüm/), 'Sertifika yenilendi.');
    await user.click(within(form).getByRole('button', { name: 'Durumu güncelle' }));
    await screen.findByText('Kayıt güncellendi');
    expect(api.patches[0]?.body).toEqual({
      status: 'resolved',
      resolution: 'Sertifika yenilendi.',
    });
  });

  it('sunucunun geçiş hatası (422) formda gösterilir', async () => {
    const incident = baseIncident();
    mockIncidentApi(incident);
    server.use(
      http.patch(apiUrl(`/incidents/${incident.id}`), () =>
        HttpResponse.json(
          { detail: 'Durum geçişi geçersiz: in_progress -> cancelled' },
          { status: 422 },
        ),
      ),
    );
    const { user } = renderDetail(incident.id);
    const form = await screen.findByRole('form', { name: 'Durum değiştir' });
    await user.selectOptions(within(form).getByLabelText(/Yeni durum/), 'cancelled');
    await user.click(within(form).getByRole('button', { name: 'Durumu güncelle' }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('Durum geçişi geçersiz');
  });

  it('durum değişikliğinde 409 da çakışma penceresi açar', async () => {
    const incident = baseIncident();
    const api = mockIncidentApi(incident);
    const { user } = renderDetail(incident.id);
    const form = await screen.findByRole('form', { name: 'Durum değiştir' });
    await user.selectOptions(within(form).getByLabelText(/Yeni durum/), 'assigned');
    api.incident = { ...api.incident, version: 9 };
    await user.click(within(form).getByRole('button', { name: 'Durumu güncelle' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('Durum: Atandı')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Güncel kaydı yükle' }));
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Sürüm').nextElementSibling).toHaveTextContent('9');
  });

  it('kapalı kayıt: durum değiştirilemez, düzenleme ve triage kapalı', async () => {
    const incident = makeIncident({ status: 'closed', resolution: 'Çözüldü.' });
    mockIncidentApi(incident);
    renderDetail(incident.id);
    expect(await screen.findByText(/durumu artık değiştirilemez/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Düzenle' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Yapay zekâ ile sınıflandır' })).toBeDisabled();
  });
});

describe('IncidentDetailPage — yapay zekâ', () => {
  it('karar panelinde sonuç, güven, gerekçe, fark, KB kaynakları ve notlar', async () => {
    const incident = baseIncident(); // v3, category=ag, impact=low, urgency=medium, P4
    const pending = makeDecision({
      incident_id: incident.id,
      outcome: 'needs_approval',
      confidence: 0.64,
      rationale: 'Birden çok kullanıcı etkileniyor.\n<script>alert(1)</script>',
      sources: ['kb-vpn-1'],
      incident_version: 2, // bayat
      output: {
        proposed: { category: 'ag', impact: 'high', urgency: 'medium', service: 'vpn' },
        applied_fields: [],
        pending_fields: ['category', 'impact', 'service', 'urgency'],
        dropped_kb_ids: ['KB404'],
        off_taxonomy: { assignment_group: 'uzay-ekibi' },
        current_priority: 'P4',
        proposed_priority: 'P2',
        policy_reason: 'P4 -> P2 yükseltme insan onayı gerektirir.',
        suggested_resolution: 'VPN sertifikasını yenileyin.',
      },
    });
    const escalated = makeDecision({
      incident_id: incident.id,
      outcome: 'escalated',
      confidence: 0,
      rationale: '',
      output: { error: 'invalid_output', policy_reason: 'LLM çıktısı şemaya uymuyor.' },
      created_at: '2026-10-11T09:00:00Z',
    });
    mockIncidentApi(incident, [pending, escalated]);
    renderDetail(incident.id);

    const list = await screen.findByRole('list', { name: /Yapay zekâ kararları/ });
    const cards = within(list).getAllByRole('article');
    expect(cards).toHaveLength(2);
    const card = cards[0]!;
    expect(within(card).getByText('Onay bekliyor')).toBeInTheDocument();
    expect(within(card).getByRole('meter', { name: 'Güven' })).toHaveAttribute(
      'aria-valuenow',
      '64',
    );
    expect(within(card).getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(card.querySelector('script')).toBeNull();
    expect(within(card).getByRole('link', { name: 'kb-vpn-1' })).toHaveAttribute(
      'href',
      '/bilgi-bankasi/kb-vpn-1',
    );
    expect(
      within(card).getByText('P4 -> P2 yükseltme insan onayı gerektirir.'),
    ).toBeInTheDocument();

    const table = within(card).getByRole('table', { name: 'Önerilen ve güncel değerler' });
    const row = (label: string) =>
      within(table).getByRole('rowheader', { name: label }).closest('tr')!;
    expect(
      within(row('Etki'))
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual([expect.stringMatching(/^Düşük/), expect.stringMatching(/^Yüksek/), 'Uygulanmadı']);
    expect(within(row('Kategori')).getByText('Değişiklik yok')).toBeInTheDocument();
    expect(
      within(row('Öncelik'))
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['P4 · Düşük', 'P2 · Yüksek', 'Uygulanmadı']);

    expect(within(card).getByText('VPN sertifikasını yenileyin.')).toBeInTheDocument();
    expect(
      within(card).getByText(/Doğrulanamayan bilgi bankası kaynakları atıldı/),
    ).toHaveTextContent('KB404');
    expect(within(card).getByText(/Sınıf listesinde olmayan/)).toBeInTheDocument();
    expect(within(card).getByText('Atama grubu: “uzay-ekibi”')).toBeInTheDocument();
    expect(within(card).getByRole('note')).toHaveTextContent(/yeniden triage gerekli/);
    // Agent onaylayamaz
    expect(within(card).queryByRole('link', { name: 'Onay kutusunda aç' })).not.toBeInTheDocument();

    const second = cards[1]!;
    expect(within(second).getByText('İnsana yönlendirildi')).toBeInTheDocument();
    expect(within(second).getByText(/şemaya uymadı/)).toBeInTheDocument();
    expect(within(second).getByText('Kaynak gösterilmedi.')).toBeInTheDocument();
  });

  it('"Yapay zekâ ile sınıflandır" triage çalıştırır, kararı ve kaydı yeniler', async () => {
    const incident = baseIncident();
    const result = makeDecision({
      incident_id: incident.id,
      outcome: 'auto_applied',
      confidence: 0.91,
      rationale: 'Ağ kategorisi doğrulandı.',
      sources: ['kb-1'],
      incident_version: 4,
      output: {
        proposed: { category: 'ag', service: 'vpn' },
        applied_fields: ['category', 'service'],
      },
    });
    const api = mockIncidentApi(incident, [], { triageResult: result });
    const { user } = renderDetail(incident.id);
    await screen.findByText('Henüz yapay zekâ kararı yok');
    api.incident = { ...api.incident, version: 4 };

    await user.click(screen.getByRole('button', { name: 'Yapay zekâ ile sınıflandır' }));
    expect(await screen.findByText('Yapay zekâ kararı: Otomatik uygulandı')).toBeInTheDocument();
    expect(api.triageCalls).toBe(1);
    const list = await screen.findByRole('list', { name: /Yapay zekâ kararları/ });
    expect(within(list).getByText('Ağ kategorisi doğrulandı.')).toBeInTheDocument();
    expect(within(list).getAllByText('Uygulandı')).toHaveLength(2);
    await waitFor(() => {
      expect(screen.getByText('Sürüm').nextElementSibling).toHaveTextContent('4');
    });
  });

  it('triage hatası bildirim olarak gösterilir', async () => {
    const incident = baseIncident();
    mockIncidentApi(incident, [], { triageStatus: 503 });
    const { user } = renderDetail(incident.id);
    await screen.findByText('Henüz yapay zekâ kararı yok');
    await user.click(screen.getByRole('button', { name: 'Yapay zekâ ile sınıflandır' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Sınıflandırma yapılamadı');
    expect(alert).toHaveTextContent('Yapay zekâ hizmeti şu anda kullanılamıyor');
  });

  it('kararlar yüklenemezse hata ve yeniden deneme', async () => {
    const incident = baseIncident();
    mockIncidentApi(incident);
    let fail = true;
    server.use(
      http.get(apiUrl(`/incidents/${incident.id}/decisions`), () =>
        fail
          ? HttpResponse.json({ detail: 'Bu işlem için yetkiniz yok.' }, { status: 403 })
          : HttpResponse.json([]),
      ),
    );
    const { user } = renderDetail(incident.id);
    expect(await screen.findByText('Kararlar yüklenemedi')).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole('button', { name: 'Yeniden dene' }));
    expect(await screen.findByText('Henüz yapay zekâ kararı yok')).toBeInTheDocument();
  });
});
