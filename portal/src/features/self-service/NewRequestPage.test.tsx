import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { makeIncident } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { server } from '@/test/server';
import { AGENT, REQUESTER, renderApp, renderWithProviders } from '@/test/render';
import NewRequestPage from './NewRequestPage';

type Body = Record<string, unknown>;

/** POST /incidents'i yakalar; gövdeyi kaydeder */
function capturePost(respond: (body: Body) => Response) {
  const bodies: Body[] = [];
  server.use(
    http.post(apiUrl('/incidents'), async ({ request }) => {
      const body = (await request.json()) as Body;
      bodies.push(body);
      return respond(body);
    }),
  );
  return bodies;
}

async function setup(user = REQUESTER) {
  const utils = renderWithProviders(<NewRequestPage />, { user, route: '/talep' });
  await screen.findByRole('heading', { level: 1, name: 'Yeni talep' });
  return utils;
}

async function fillValid(u: Awaited<ReturnType<typeof setup>>['user']) {
  await u.type(screen.getByRole('textbox', { name: /Başlık/ }), 'VPN bağlanmıyor');
  await u.type(
    screen.getByRole('textbox', { name: /Açıklama/ }),
    'Evden VPN ile bağlanınca zaman aşımı alıyorum.',
  );
}

describe('NewRequestPage (/talep)', () => {
  it('parola uyarısı, KVKK notu ve süreç bilgisi gösterilir', async () => {
    await setup();
    expect(screen.getByRole('note', { name: 'Parolanızı asla yazmayın' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Kişisel verileriniz (KVKK)' })).toBeInTheDocument();
    expect(screen.getByText(/6698 sayılı/)).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Gönderdikten sonra ne olur?' }),
    ).toBeInTheDocument();
    // Hizmet listesi taksonomiden; etki alanı self-serviste yok
    const service = screen.getByRole('combobox', { name: /Hizmet/ });
    expect(within(service).getByRole('option', { name: 'Uzaktan erişim VPN' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /Etki/ })).not.toBeInTheDocument();
  });

  it('mutlu yol (requester): gönderir, kayıt numarasını ve sonraki adımları gösterir', async () => {
    const bodies = capturePost((body) =>
      HttpResponse.json(
        makeIncident({
          number: 'INC0000042',
          title: String(body.title),
          description: String(body.description),
          service: (body.service as string | undefined) ?? null,
        }),
        { status: 201 },
      ),
    );
    const { user } = await setup();
    await fillValid(user);
    await user.selectOptions(screen.getByRole('combobox', { name: /Hizmet/ }), 'vpn');
    await user.selectOptions(screen.getByRole('combobox', { name: /Aciliyet/ }), 'high');
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));

    const heading = await screen.findByRole('heading', { level: 1, name: 'Talebiniz alındı' });
    expect(heading).toHaveFocus();
    expect(screen.getByTestId('ticket-number')).toHaveTextContent('INC0000042');
    expect(screen.getByText('Yapay zekâ ön değerlendirmesi')).toBeInTheDocument();
    expect(screen.getByText('Destek uzmanı inceler')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Taleplerimi gör' })).toHaveAttribute(
      'href',
      '/taleplerim',
    );
    expect(document.title).toBe('Talebiniz alındı · ServisZeka');

    expect(bodies).toEqual([
      {
        title: 'VPN bağlanmıyor',
        description: 'Evden VPN ile bağlanınca zaman aşımı alıyorum.',
        kind: 'incident',
        service: 'vpn',
        urgency: 'high',
      },
    ]);
    // requester: caller/impact gönderilmez
    expect(bodies[0]).not.toHaveProperty('caller');
    expect(bodies[0]).not.toHaveProperty('impact');

    // Yeni talep → boş form
    await user.click(screen.getByRole('button', { name: 'Yeni talep oluştur' }));
    expect(await screen.findByRole('textbox', { name: /Başlık/ })).toHaveValue('');
  });

  it('agent kendi adına açarken caller olarak kullanıcı adını gönderir', async () => {
    const bodies = capturePost(() =>
      HttpResponse.json(makeIncident({ number: 'INC0000077' }), { status: 201 }),
    );
    const { user } = await setup(AGENT);
    await fillValid(user);
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));
    expect(await screen.findByTestId('ticket-number')).toHaveTextContent('INC0000077');
    expect(bodies[0]).toMatchObject({ caller: 'ayse' });
    expect(bodies[0]).not.toHaveProperty('service');
  });

  it('istemci doğrulaması: hata özeti, alan hataları, istek gönderilmez', async () => {
    const bodies = capturePost(() => HttpResponse.json(makeIncident(), { status: 201 }));
    const { user } = await setup();
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));

    const summary = await screen.findByRole('alert');
    expect(within(summary).getByText('Lütfen şu alanları düzeltin:')).toBeInTheDocument();
    await waitFor(() => {
      expect(summary).toHaveFocus();
    });
    expect(within(summary).getByRole('link', { name: /Başlık/ })).toHaveAttribute(
      'href',
      '#talep-baslik',
    );
    const title = screen.getByRole('textbox', { name: /Başlık/ });
    expect(title).toHaveAttribute('aria-invalid', 'true');
    expect(title).toHaveAccessibleDescription(/Kısa bir başlık yazın/);
    expect(screen.getByRole('textbox', { name: /Açıklama/ })).toHaveAttribute(
      'aria-invalid',
      'true',
    );

    // Yazınca alan hatası kalkar
    await user.type(title, 'Yazıcı çalışmıyor');
    expect(title).not.toHaveAttribute('aria-invalid');
    expect(bodies).toHaveLength(0);
  });

  it('parola içeren açıklama gönderilmez', async () => {
    const bodies = capturePost(() => HttpResponse.json(makeIncident(), { status: 201 }));
    const { user } = await setup();
    await user.type(screen.getByRole('textbox', { name: /Başlık/ }), 'Giriş yapamıyorum');
    await user.type(
      screen.getByRole('textbox', { name: /Açıklama/ }),
      'Kullanıcı adım zeynep, parola: Yaz2026!',
    );
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/parola paylaşılmış gibi/);
    expect(bodies).toHaveLength(0);
  });

  it('sunucu 422 alan hatası ilgili alana yazılır', async () => {
    capturePost(() =>
      HttpResponse.json(
        {
          detail: [
            {
              loc: ['body', 'title'],
              msg: 'String should have at most 300 characters',
              type: 'string_too_long',
            },
          ],
        },
        { status: 422 },
      ),
    );
    const { user } = await setup();
    await fillValid(user);
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));
    const summary = await screen.findByRole('alert');
    expect(summary).toHaveTextContent('Başlık: String should have at most 300 characters');
    expect(screen.getByRole('textbox', { name: /Başlık/ })).toHaveAttribute('aria-invalid', 'true');
  });

  it('sunucu hatasında satır içi uyarı gösterilir ve girilenler korunur', async () => {
    capturePost(() => HttpResponse.json({ detail: 'Veritabanı meşgul.' }, { status: 503 }));
    const { user } = await setup();
    await fillValid(user);
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('heading', { name: 'Talebiniz gönderilemedi' })).toBeVisible();
    expect(alert).toHaveTextContent('Veritabanı meşgul.');
    expect(screen.getByRole('textbox', { name: /Başlık/ })).toHaveValue('VPN bağlanmıyor');
    expect(screen.queryByRole('heading', { name: 'Talebiniz alındı' })).not.toBeInTheDocument();
  });

  it('ağ hatasında Türkçe bağlantı mesajı gösterilir', async () => {
    server.use(http.post(apiUrl('/incidents'), () => HttpResponse.error()));
    const { user } = await setup();
    await fillValid(user);
    await user.click(screen.getByRole('button', { name: 'Talebi gönder' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Sunucuya ulaşılamadı/);
  });

  it('rota tablosunda /talep requester için açılır', async () => {
    renderApp({ user: REQUESTER, route: '/talep' });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Yeni talep' }),
    ).toBeInTheDocument();
  });
});
