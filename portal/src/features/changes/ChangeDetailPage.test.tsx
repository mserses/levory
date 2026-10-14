import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { apiUrl } from '@/test/handlers';
import { AGENT, renderWithProviders, type TestUser } from '@/test/render';
import { server } from '@/test/server';
import ChangeDetailPage from './ChangeDetailPage';
import {
  makeApproval,
  makeCI,
  makeChange,
  makeImpact,
  makeRiskDecision,
  mockChangeApi,
} from './testUtils';

const CAB: TestUser = { username: 'kurul1', roles: ['cab'] };
const MANAGER: TestUser = { username: 'mudur', roles: ['change_manager'] };

function renderDetail(id: string, user: TestUser = AGENT) {
  return renderWithProviders(<ChangeDetailPage />, {
    user,
    route: `/degisiklikler/${id}`,
    path: '/degisiklikler/:id',
  });
}

describe('ChangeDetailPage — başlık ve aşama göstergesi', () => {
  it('numara, başlık, rozetler ve mevcut aşama', async () => {
    const change = makeChange({
      number: 'CHG0000011',
      title: 'Yük dengeleyici yapılandırması',
      state: 'authorize',
      change_type: 'normal',
      risk_level: 'medium',
      risk_score: 42,
    });
    mockChangeApi(change);
    renderDetail(change.id);
    expect(
      await screen.findByRole('heading', { level: 1, name: /Yük dengeleyici yapılandırması/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('CHG0000011')).toBeInTheDocument();
    const stepper = screen.getByRole('list', { name: 'Değişiklik aşamaları' });
    const current = within(stepper).getByText('Yetkilendirme (CAB)').closest('li');
    expect(current).toHaveAttribute('aria-current', 'step');
    expect(within(stepper).getAllByText(/tamamlandı/)).toHaveLength(2);
    expect(screen.getAllByText(/Risk: Orta \(42\)/).length).toBeGreaterThan(0);
  });

  it('bulunamayan değişiklik', async () => {
    server.use(
      http.get(apiUrl('/changes/yok'), () =>
        HttpResponse.json({ detail: 'Değişiklik bulunamadı' }, { status: 404 }),
      ),
    );
    renderDetail('yok');
    expect(
      await screen.findByRole('heading', { name: 'Değişiklik bulunamadı' }),
    ).toBeInTheDocument();
  });

  it('etki analizi gösterilir', async () => {
    const root = makeCI({ name: 'erp-db-01' });
    const app = makeCI({ name: 'erp-app', ci_class: 'application', criticality: 1 });
    const change = makeChange({ affected_ci_ids: [root.id] });
    const api = mockChangeApi(change);
    api.impact = makeImpact([root], [{ ci: app, depth: 1, path: [root.id, app.id] }]);
    renderDetail(change.id);
    const region = await screen.findByRole('region', { name: 'Etki analizi' });
    expect(await within(region).findByRole('link', { name: 'erp-app' })).toHaveAttribute(
      'href',
      `/cmdb/${app.id}`,
    );
    expect(within(region).queryByText(/sınırında kesildi/)).not.toBeInTheDocument();
  });

  it('kesilen etki analizi uyarı olarak gösterilir', async () => {
    const root = makeCI({ name: 'erp-db-01' });
    const app = makeCI({ name: 'erp-app', ci_class: 'application' });
    const change = makeChange({ affected_ci_ids: [root.id] });
    const api = mockChangeApi(change);
    api.impact = {
      ...makeImpact([root], [{ ci: app, depth: 1, path: [root.id, app.id] }]),
      truncated: true,
    };
    renderDetail(change.id);
    const region = await screen.findByRole('region', { name: 'Etki analizi' });
    expect(await within(region).findByRole('alert')).toHaveTextContent(
      /Etki analizi 1 CI sınırında kesildi/,
    );
  });
});

describe('ChangeDetailPage — risk paneli', () => {
  it('deterministik döküm + yapay zekâ anlatısı, sonuç rozeti ve güven ölçeri', async () => {
    const change = makeChange({ state: 'assess', risk_level: 'high', risk_score: 62 });
    mockChangeApi(change, { decisions: [makeRiskDecision(change)] });
    renderDetail(change.id);
    const panel = await screen.findByRole('region', { name: 'Risk değerlendirmesi' });
    const breakdown = await within(panel).findByRole('table', { name: /Deterministik risk/ });
    const row = within(breakdown).getByText('En yüksek CI kritikliği').closest('tr')!;
    expect(within(row).getByText('+25')).toBeInTheDocument();
    expect(within(breakdown).getByText('60')).toBeInTheDocument();
    expect(within(panel).getByText(/Üretim veritabanı etkileniyor/)).toBeInTheDocument();
    expect(within(panel).getByText('Yedeği doğrula')).toBeInTheDocument();
    expect(within(panel).getByText('Otomatik uygulandı')).toBeInTheDocument();
    expect(within(panel).getByRole('meter', { name: 'Güven' })).toHaveAttribute(
      'aria-valuenow',
      '86',
    );
    expect(within(panel).queryByText('criticality')).not.toBeInTheDocument();
  });

  it('"Riski değerlendir" POST /assess çağırır ve sonucu gösterir', async () => {
    const change = makeChange({ state: 'draft' });
    const api = mockChangeApi(change, {
      assessResult: makeRiskDecision(change, { outcome: 'suggested', rationale: 'Gölge mod.' }),
    });
    const { user } = renderDetail(change.id);
    const panel = await screen.findByRole('region', { name: 'Risk değerlendirmesi' });
    expect(
      await within(panel).findByText(/henüz yapay zekâ risk değerlendirmesi yok/i),
    ).toBeInTheDocument();
    await user.click(within(panel).getByRole('button', { name: 'Riski değerlendir' }));
    await waitFor(() => {
      expect(api.assessCalls).toBe(1);
    });
    expect(await within(panel).findByText('Öneri')).toBeInTheDocument();
    expect(await screen.findByText(/Yapay zekâ risk kararı: Öneri/)).toBeInTheDocument();
  });

  it('"Riski değerlendir" yalnızca taslak/değerlendirmede ve CAB üyesine gösterilmez', async () => {
    const change = makeChange({ state: 'authorize' });
    mockChangeApi(change);
    const { unmount } = renderDetail(change.id, MANAGER);
    await screen.findByRole('region', { name: 'Risk değerlendirmesi' });
    expect(screen.queryByRole('button', { name: 'Riski değerlendir' })).not.toBeInTheDocument();
    unmount();
    const draft = makeChange({ state: 'draft' });
    mockChangeApi(draft);
    renderDetail(draft.id, CAB);
    await screen.findByRole('region', { name: 'Risk değerlendirmesi' });
    expect(screen.queryByRole('button', { name: 'Riski değerlendir' })).not.toBeInTheDocument();
  });

  it('riski düşüren öneri: yalnızca change_manager onaylar (kapı)', async () => {
    const change = makeChange({ state: 'assess', risk_score: 55, risk_level: 'high' });
    const lower = makeRiskDecision(
      change,
      { outcome: 'needs_approval' },
      {
        proposed: { risk_score: 40, risk_level: 'medium' },
        current_risk_score: 55,
        applied: false,
      },
    );
    const api = mockChangeApi(change, { decisions: [lower] });
    const { unmount } = renderDetail(change.id, AGENT);
    const panel = await screen.findByRole('region', { name: 'Risk değerlendirmesi' });
    expect(await within(panel).findByText(/riski düşürüyor/i)).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: 'Öneriyi onayla' })).not.toBeInTheDocument();
    expect(within(panel).getByText(/değişiklik yöneticisi onayı gerekir/i)).toBeInTheDocument();
    unmount();

    const { user } = renderDetail(change.id, MANAGER);
    const managerPanel = await screen.findByRole('region', { name: 'Risk değerlendirmesi' });
    await user.click(await within(managerPanel).findByRole('button', { name: 'Öneriyi onayla' }));
    await waitFor(() => {
      expect(api.approvedDecisions).toEqual([lower.id]);
    });
  });
});

describe('ChangeDetailPage — CAB onayları', () => {
  it('onay listesi ve durum özeti', async () => {
    const change = makeChange({ state: 'authorize' });
    mockChangeApi(change, {
      approvals: [makeApproval({ approver: 'kurul2', comment: 'Plan yeterli.' })],
    });
    renderDetail(change.id, CAB);
    const region = await screen.findByRole('region', { name: 'CAB onayları' });
    expect(await within(region).findByText('kurul2')).toBeInTheDocument();
    expect(within(region).getByText('Onayladı')).toBeInTheDocument();
    expect(within(region).getByText('Plan yeterli.')).toBeInTheDocument();
    expect(within(region).getByText(/Onay 1\/2/)).toBeInTheDocument();
  });

  it('CAB üyesi yorumla onay verir', async () => {
    const change = makeChange({ state: 'authorize' });
    const api = mockChangeApi(change);
    const { user } = renderDetail(change.id, CAB);
    const region = await screen.findByRole('region', { name: 'CAB onayları' });
    await user.click(await within(region).findByRole('radio', { name: 'Onayla' }));
    await user.type(within(region).getByRole('textbox', { name: /Yorum/ }), 'Uygun.');
    await user.click(within(region).getByRole('button', { name: 'Oyu gönder' }));
    await waitFor(() => {
      expect(api.votes).toEqual([{ decision: 'approve', comment: 'Uygun.' }]);
    });
    expect(
      await within(region).findByText(/Bu değişiklik için kararınızı verdiniz/),
    ).toBeInTheDocument();
  });

  it('ret yorum ister ve onay penceresiyle doğrulanır', async () => {
    const change = makeChange({ state: 'authorize' });
    const api = mockChangeApi(change);
    const { user } = renderDetail(change.id, CAB);
    const region = await screen.findByRole('region', { name: 'CAB onayları' });
    await user.click(await within(region).findByRole('radio', { name: 'Reddet' }));
    await user.click(within(region).getByRole('button', { name: 'Oyu gönder' }));
    expect(await within(region).findByText('Ret için gerekçe yazın.')).toBeInTheDocument();
    await user.type(within(region).getByRole('textbox', { name: /Yorum/ }), 'Geri dönüş zayıf.');
    await user.click(within(region).getByRole('button', { name: 'Oyu gönder' }));
    const dialog = await screen.findByRole('alertdialog', { name: /reddedilsin mi/i });
    await user.click(within(dialog).getByRole('button', { name: 'Reddet' }));
    await waitFor(() => {
      expect(api.votes).toEqual([{ decision: 'reject', comment: 'Geri dönüş zayıf.' }]);
    });
  });

  it('talep eden ya da uygulayıcı oy veremez: form devre dışı ve gerekçeli', async () => {
    const change = makeChange({ state: 'authorize', requested_by: 'ayse', assignee: 'kurul1' });
    mockChangeApi(change);
    renderDetail(change.id, CAB);
    const region = await screen.findByRole('region', { name: 'CAB onayları' });
    const submit = await within(region).findByRole('button', { name: 'Oyu gönder' });
    expect(submit).toBeDisabled();
    expect(within(region).getByRole('radio', { name: 'Onayla' })).toBeDisabled();
    expect(within(region).getByText(/Uygulayıcısı olduğunuz değişiklik/)).toBeInTheDocument();
  });

  it('agent oy formunu görmez', async () => {
    const change = makeChange({ state: 'authorize' });
    mockChangeApi(change);
    renderDetail(change.id, AGENT);
    const region = await screen.findByRole('region', { name: 'CAB onayları' });
    await within(region).findByText(/Onay 0\/2/);
    expect(within(region).queryByRole('button', { name: 'Oyu gönder' })).not.toBeInTheDocument();
  });
});

describe('ChangeDetailPage — durum geçişleri', () => {
  it('yalnızca izinli geçişler: talep eden taslağı değerlendirmeye gönderir (If-Match)', async () => {
    const change = makeChange({ state: 'draft', requested_by: 'ayse', version: 3 });
    const api = mockChangeApi(change);
    const { user } = renderDetail(change.id, AGENT);
    const region = await screen.findByRole('region', { name: 'İşlemler' });
    const buttons = within(region)
      .getAllByRole('button')
      .map((b) => b.textContent);
    expect(buttons).toEqual(['Değerlendirmeye gönder', 'İptal et']);
    await user.click(within(region).getByRole('button', { name: 'Değerlendirmeye gönder' }));
    await waitFor(() => {
      expect(api.transitions).toEqual([{ body: { to_state: 'assess' }, ifMatch: '"3"' }]);
    });
    expect(await screen.findByText(/Değerlendirme aşamasına geçti/)).toBeInTheDocument();
  });

  it('CAB üyesine geçiş düğmesi gösterilmez', async () => {
    const change = makeChange({ state: 'authorize' });
    mockChangeApi(change);
    renderDetail(change.id, CAB);
    const region = await screen.findByRole('region', { name: 'İşlemler' });
    expect(within(region).queryAllByRole('button')).toHaveLength(0);
    expect(within(region).getByText(/yapabileceğiniz bir durum geçişi yok/i)).toBeInTheDocument();
  });

  it('iptal onay ister', async () => {
    const change = makeChange({ state: 'draft', requested_by: 'ayse' });
    const api = mockChangeApi(change);
    const { user } = renderDetail(change.id, AGENT);
    await user.click(await screen.findByRole('button', { name: 'İptal et' }));
    const dialog = await screen.findByRole('alertdialog', { name: /iptal edilsin mi/i });
    await user.click(within(dialog).getByRole('button', { name: 'İptal et' }));
    await waitFor(() => {
      expect(api.transitions[0]?.body).toEqual({ to_state: 'cancelled' });
    });
  });

  it('sürüm çakışması (409): diyalog ve güncel hâli yükleme', async () => {
    const change = makeChange({ state: 'draft', requested_by: 'ayse', version: 1 });
    const api = mockChangeApi(change);
    const { user } = renderDetail(change.id, AGENT);
    await screen.findByRole('button', { name: 'Değerlendirmeye gönder' });
    // Başkası değiştirdi
    api.change = { ...api.change, version: 2, title: 'Başkası güncelledi' };
    await user.click(screen.getByRole('button', { name: 'Değerlendirmeye gönder' }));
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Değişiklik başkası tarafından güncellendi',
    });
    await user.click(within(dialog).getByRole('button', { name: 'Güncel hâli yükle' }));
    expect(
      await screen.findByRole('heading', { level: 1, name: /Başkası güncelledi/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('zamanlama çakışması (409) ayrıntılarıyla gösterilir', async () => {
    const change = makeChange({ state: 'authorize', version: 4 });
    const api = mockChangeApi(change);
    api.scheduleConflicts = [
      {
        kind: 'freeze_window',
        ref_id: 'fw-1',
        ref_number: null,
        start: '2026-10-20T00:00:00Z',
        end: '2026-10-21T00:00:00Z',
        description: 'Ay sonu kapanışı',
      },
    ];
    const { user } = renderDetail(change.id, MANAGER);
    await user.click(await screen.findByRole('button', { name: 'Zamanla' }));
    const dialog = await screen.findByRole('alertdialog', { name: /çakışma/i });
    expect(within(dialog).getByText(/Ay sonu kapanışı/)).toBeInTheDocument();
  });

  it('sunucu kuralı reddederse (422) mesaj gösterilir', async () => {
    const change = makeChange({ state: 'draft', requested_by: 'ayse' });
    mockChangeApi(change);
    server.use(
      http.post(apiUrl(`/changes/${change.id}/transition`), () =>
        HttpResponse.json(
          { detail: 'Değişiklik taslaktan çıkamaz; eksik: geri dönüş planı.' },
          { status: 422 },
        ),
      ),
    );
    const { user } = renderDetail(change.id, AGENT);
    await user.click(await screen.findByRole('button', { name: 'Değerlendirmeye gönder' }));
    expect(await screen.findByText(/eksik: geri dönüş planı/)).toBeInTheDocument();
  });
});

describe('ChangeDetailPage — uygulama sonrası gözden geçirme (PIR)', () => {
  it('acil değişiklik PIR notu olmadan kapatılamaz; notla kapatılır', async () => {
    const change = makeChange({
      state: 'review',
      change_type: 'emergency',
      implementation_outcome: 'succeeded',
      version: 7,
    });
    const api = mockChangeApi(change);
    const { user } = renderDetail(change.id, MANAGER);
    const form = await screen.findByRole('form', { name: 'Uygulama sonrası gözden geçirme' });
    await user.click(
      within(form).getByRole('button', { name: 'Gözden geçirmeyi kaydet ve kapat' }),
    );
    expect(
      await within(form).findByText(/Acil ya da başarısız değişiklikte PIR notu zorunludur/),
    ).toBeInTheDocument();
    expect(api.transitions).toHaveLength(0);
    await user.type(within(form).getByRole('textbox', { name: /PIR/ }), 'Kök neden: disk doldu.');
    await user.click(
      within(form).getByRole('button', { name: 'Gözden geçirmeyi kaydet ve kapat' }),
    );
    await waitFor(() => {
      expect(api.transitions).toEqual([
        {
          body: { to_state: 'closed', review_notes: 'Kök neden: disk doldu.' },
          ifMatch: '"7"',
        },
      ]);
    });
  });

  it('PIR formu yalnızca kapatma yetkisi olana (change_manager)', async () => {
    const change = makeChange({ state: 'review' });
    mockChangeApi(change);
    renderDetail(change.id, AGENT);
    await screen.findByRole('region', { name: 'İşlemler' });
    expect(
      screen.queryByRole('form', { name: 'Uygulama sonrası gözden geçirme' }),
    ).not.toBeInTheDocument();
  });

  it('kapalı değişiklikte PIR notu salt okunur gösterilir', async () => {
    const change = makeChange({ state: 'closed', review_notes: 'Her şey yolunda.' });
    mockChangeApi(change);
    renderDetail(change.id, MANAGER);
    expect(await screen.findByText('Her şey yolunda.')).toBeInTheDocument();
  });
});
