import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeCatalogItem, makeServiceRequest } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import CatalogItemPage from './CatalogItemPage';

const mailbox = makeCatalogItem({
  key: 'shared_mailbox_access',
  name: 'Paylaşılan posta kutusu erişimi',
  description: 'Ekibinizin paylaşılan posta kutusuna erişim verilir.',
  category: 'E-posta ve iş birliği',
  standard: false,
  approval_policy: 'manager',
  form_schema: {
    type: 'object',
    properties: {
      mailbox: { type: 'string', format: 'user', title: 'Posta kutusu' },
      team: { type: 'string', format: 'group', title: 'Ekip grubu' },
      access: {
        type: 'string',
        enum: ['full_access', 'send_as'],
        title: 'Erişim türü',
        default: 'full_access',
      },
      remote: { type: 'boolean', title: 'Uzaktan erişim de gerekli' },
      justification: { type: 'string', title: 'Gerekçe', maxLength: 500, minLength: 3 },
    },
    required: ['mailbox', 'justification'],
  },
  fulfilment_plan: [
    { tool: 'mailbox.grant_shared_access', title: 'Posta kutusu izni ver', args: {} },
    { tool: 'notify.send_to_user', title: 'Talep edeni bilgilendir', args: {} },
  ],
});

function mockItem(item = mailbox, status = 200) {
  server.use(
    http.get(apiUrl('/catalog/items/:key'), ({ params }) =>
      params.key === item.key
        ? HttpResponse.json(item, { status })
        : HttpResponse.json({ detail: 'Katalog öğesi bulunamadı' }, { status: 404 }),
    ),
  );
}

function mockCreate(response: () => Response) {
  const bodies: unknown[] = [];
  server.use(
    http.post(apiUrl('/requests'), async ({ request }) => {
      bodies.push(await request.json());
      return response();
    }),
  );
  return bodies;
}

const renderPage = (user = REQUESTER, key = mailbox.key) =>
  renderWithProviders(<CatalogItemPage />, {
    user,
    route: `/katalog/${key}`,
    path: '/katalog/:key',
  });

describe('CatalogItemPage (/katalog/:key)', () => {
  it('formu şemadan erişilebilir biçimde kurar: seçiciler, seçenek, onay kutusu, metin', async () => {
    mockItem();
    renderPage();
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Paylaşılan posta kutusu erişimi' }),
    ).toBeInTheDocument();

    const user = screen.getByRole('textbox', { name: /Posta kutusu/ });
    expect(user).toBeRequired();
    expect(user).toHaveAttribute('data-picker', 'user');
    expect(user).toHaveAccessibleDescription(/Kullanıcı adı ya da e-posta/);
    expect(screen.getByRole('textbox', { name: /Ekip grubu/ })).toHaveAttribute(
      'data-picker',
      'group',
    );
    const access = screen.getByRole('combobox', { name: /Erişim türü/ });
    expect(access).toHaveValue('full_access');
    // Ham enum değeri gösterilmez
    expect(within(access).getByRole('option', { name: 'Tam erişim' })).toBeInTheDocument();
    expect(within(access).queryByRole('option', { name: 'full_access' })).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Uzaktan erişim de gerekli' })).not.toBeChecked();
    expect(screen.getByRole('textbox', { name: /Gerekçe/ })).toBeRequired();
    // Başkası adına yalnızca agent
    expect(screen.queryByRole('textbox', { name: /Başkası adına/ })).toBeNull();

    // Neler olacak: onay politikası + plan
    const aside = screen.getByRole('complementary', { name: 'Neler olacak?' });
    expect(within(aside).getByText('Yönetici onayı gerekir')).toBeInTheDocument();
    expect(
      within(aside)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(['Posta kutusu izni ver', 'Talep edeni bilgilendir']);
  });

  it('istemci doğrulaması: hata özeti odak alır, alanlar aria-invalid, istek gönderilmez', async () => {
    mockItem();
    const bodies = mockCreate(() => HttpResponse.json({}, { status: 500 }));
    const { user } = renderPage();
    await screen.findByRole('heading', { level: 1 });
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));

    const summary = await screen.findByRole('alert');
    expect(summary).toHaveFocus();
    expect(within(summary).getByRole('link', { name: 'Posta kutusu zorunludur.' })).toHaveAttribute(
      'href',
      '#katalog-alan-mailbox',
    );
    expect(screen.getByRole('textbox', { name: /Posta kutusu/ })).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await user.type(screen.getByRole('textbox', { name: /Gerekçe/ }), 'ab');
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));
    expect(
      await screen.findByText('Gerekçe en az 3 karakter olmalı.', { selector: 'a' }),
    ).toBeVisible();
    expect(bodies).toHaveLength(0);
  });

  it('gönderim: kırpılmış form değerleri → talep ayrıntısına gider', async () => {
    mockItem();
    const created = makeServiceRequest({
      number: 'REQ0000042',
      item_key: mailbox.key,
      item_name: mailbox.name,
      state: 'awaiting_approval',
      approval_state: 'pending',
      touchless: null,
      step_results: [],
    });
    const bodies = mockCreate(() => HttpResponse.json(created, { status: 201 }));
    server.use(http.get(apiUrl(`/requests/${created.id}`), () => HttpResponse.json(created)));
    const { user } = renderApp({ user: REQUESTER, route: `/katalog/${mailbox.key}` });
    await screen.findByRole('heading', { level: 1, name: mailbox.name });

    await user.click(screen.getByRole('button', { name: 'Posta kutusu: kendimi seç' }));
    expect(screen.getByRole('textbox', { name: /Posta kutusu/ })).toHaveValue('zeynep');
    await user.clear(screen.getByRole('textbox', { name: /Posta kutusu/ }));
    await user.type(
      screen.getByRole('textbox', { name: /Posta kutusu/ }),
      '  finans@kurum.com.tr ',
    );
    await user.selectOptions(screen.getByRole('combobox', { name: /Erişim türü/ }), 'send_as');
    await user.click(screen.getByRole('checkbox', { name: 'Uzaktan erişim de gerekli' }));
    await user.type(screen.getByRole('textbox', { name: /Gerekçe/ }), 'Finans ekibine katıldım');
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));

    expect(
      await screen.findByRole('heading', { level: 1, name: /REQ0000042/ }),
    ).toBeInTheDocument();
    expect(bodies[0]).toEqual({
      item: 'shared_mailbox_access',
      form_values: {
        mailbox: 'finans@kurum.com.tr',
        access: 'send_as',
        remote: true,
        justification: 'Finans ekibine katıldım',
      },
    });
  });

  it('agent başkası adına talep açabilir', async () => {
    mockItem(makeCatalogItem({ key: 'vpn_access', name: 'VPN erişim grubu', form_schema: {} }));
    const bodies = mockCreate(() => HttpResponse.json({ detail: 'Geçici hata' }, { status: 503 }));
    const { user } = renderPage(AGENT, 'vpn_access');
    await screen.findByRole('heading', { level: 1, name: 'VPN erişim grubu' });
    expect(screen.getByText(/ek bilgi gerekmiyor/)).toBeInTheDocument();
    await user.type(screen.getByRole('textbox', { name: /Başkası adına/ }), 'mehmet');
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));
    await waitFor(() => {
      expect(bodies).toEqual([{ item: 'vpn_access', form_values: {}, requester: 'mehmet' }]);
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Geçici hata');
  });

  it('sunucu doğrulama hataları (422 message + errors) özet listesinde', async () => {
    mockItem();
    mockCreate(() =>
      HttpResponse.json(
        {
          detail: {
            message: 'Form değerleri geçersiz',
            errors: ['Posta kutusu: dizinde bulunamadı'],
          },
        },
        { status: 422 },
      ),
    );
    const { user } = renderPage();
    await screen.findByRole('heading', { level: 1 });
    await user.type(screen.getByRole('textbox', { name: /Posta kutusu/ }), 'yok@kurum.com.tr');
    await user.type(screen.getByRole('textbox', { name: /Gerekçe/ }), 'Gerekli');
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));
    const summary = await screen.findByRole('alert');
    expect(summary).toHaveTextContent('Posta kutusu: dizinde bulunamadı');
    await waitFor(() => {
      expect(summary).toHaveFocus();
    });
  });

  it('bilinmeyen öğe: bulunamadı + kataloğa dönüş', async () => {
    mockItem();
    renderPage(REQUESTER, 'yok_boyle');
    expect(await screen.findByText('Hizmet bulunamadı')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Kataloğa dön' })).toHaveAttribute('href', '/katalog');
  });
});
