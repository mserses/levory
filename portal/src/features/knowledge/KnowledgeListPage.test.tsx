import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { Article, ArticleCreate } from '@/api/types';
import { makeArticle } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { AGENT, APPROVER, renderApp, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import KnowledgeListPage from './KnowledgeListPage';

function mockList(respond: (url: URL) => Article[] | Response) {
  const calls: URL[] = [];
  server.use(
    http.get(apiUrl('/knowledge/articles'), ({ request }) => {
      const url = new URL(request.url);
      calls.push(url);
      const r = respond(url);
      return r instanceof Response ? r : HttpResponse.json(r);
    }),
  );
  return calls;
}

const render = (user = AGENT, route = '/bilgi-bankasi') =>
  renderWithProviders(<KnowledgeListPage />, { user, route });

describe('KnowledgeListPage', () => {
  it('makaleleri durum, kategori ve bilinen hata etiketiyle listeler', async () => {
    mockList(() => [
      makeArticle({ number: 'KB-1', title: 'VPN sertifikası', state: 'published', category: 'ag' }),
      makeArticle({
        number: 'KB-2',
        title: 'Outlook donuyor',
        state: 'draft',
        category: 'eposta_isbirligi',
        is_known_error: true,
      }),
    ]);
    render();
    expect(await screen.findByRole('heading', { level: 1, name: 'Bilgi bankası' })).toBeVisible();
    const rows = await screen.findAllByTestId('article-row');
    expect(rows).toHaveLength(2);
    const [first, second] = rows as [HTMLElement, HTMLElement];
    expect(within(first).getByRole('link', { name: /VPN sertifikası/ })).toHaveAttribute(
      'href',
      expect.stringMatching(/^\/bilgi-bankasi\//),
    );
    expect(within(first).getByText('Yayında')).toBeInTheDocument();
    expect(within(first).getByText('Ağ ve bağlantı')).toBeInTheDocument();
    expect(within(second).getByText('Taslak')).toBeInTheDocument();
    expect(within(second).getByText('Bilinen hata')).toBeInTheDocument();
    expect(within(second).getByText('E-posta ve iş birliği')).toBeInTheDocument();
  });

  it('durum filtresi ve sayfalama sorguya yansır', async () => {
    const calls = mockList((url) =>
      Array.from({ length: url.searchParams.get('offset') === '0' ? 21 : 3 }, (_, i) =>
        makeArticle({ title: `Makale ${String(i)}` }),
      ),
    );
    const { user } = render();
    expect(await screen.findAllByTestId('article-row')).toHaveLength(20);
    expect(calls[0]?.searchParams.get('limit')).toBe('21');
    expect(calls[0]?.searchParams.has('state')).toBe(false);

    await user.selectOptions(screen.getByLabelText('Durum'), 'Taslak');
    await waitFor(() => {
      expect(calls.at(-1)?.searchParams.get('state')).toBe('draft');
    });

    await user.click(await screen.findByRole('button', { name: 'Sonraki →' }));
    await waitFor(() => {
      expect(calls.at(-1)?.searchParams.get('offset')).toBe('20');
    });
    expect(await screen.findByText('Sayfa 2')).toBeInTheDocument();
    expect(calls.at(-1)?.searchParams.get('state')).toBe('draft');
    expect(screen.getByRole('button', { name: 'Sonraki →' })).toBeDisabled();
  });

  it('arama sonuçlarını güvenli biçimde vurgular (HTML yorumlanmaz)', async () => {
    mockList(() => []);
    let searched: URL | null = null;
    server.use(
      http.get(apiUrl('/knowledge/search'), ({ request }) => {
        searched = new URL(request.url);
        return HttpResponse.json({
          query: 'vpn sertifika',
          hits: [
            {
              article_id: 'a1',
              number: 'KB0000001',
              title: 'VPN sertifikası nasıl yenilenir?',
              snippet: '<img src=x onerror="alert(1)"> Sertifikanızın süresi dolduysa VPN…',
              score: 0.93,
            },
          ],
        });
      }),
    );
    const { user, container } = render();
    await user.type(
      await screen.findByRole('searchbox', { name: 'Makalelerde ara' }),
      'vpn sertifika',
    );
    await user.click(screen.getByRole('button', { name: 'Ara' }));

    const hit = await screen.findByTestId('search-hit');
    expect(searched!.searchParams.get('q')).toBe('vpn sertifika');
    expect(screen.getByText('“vpn sertifika” için 1 sonuç')).toBeInTheDocument();
    const marks = Array.from(hit.querySelectorAll('mark')).map((m) => m.textContent);
    expect(marks).toEqual(['VPN', 'sertifikası', 'Sertifikanızın', 'VPN']);
    expect(container.querySelector('img')).toBeNull();
    expect(hit).toHaveTextContent('<img src=x onerror="alert(1)">');
    expect(within(hit).getByRole('link', { name: /VPN sertifikası/ })).toHaveAttribute(
      'href',
      '/bilgi-bankasi/a1',
    );

    await user.click(screen.getByRole('button', { name: 'Aramayı temizle' }));
    expect(screen.queryByTestId('search-hit')).not.toBeInTheDocument();
  });

  it('sonuçsuz arama boş durum gösterir (adresteki sorgu ile)', async () => {
    mockList(() => []);
    server.use(
      http.get(apiUrl('/knowledge/search'), ({ request }) =>
        new URL(request.url).searchParams.get('q') === 'yok'
          ? HttpResponse.json({ query: 'yok', hits: [] })
          : HttpResponse.json({ detail: 'Arama motoru hatası' }, { status: 500 }),
      ),
    );
    render(AGENT, '/bilgi-bankasi?q=yok');
    expect(await screen.findByRole('heading', { name: 'Eşleşen makale yok' })).toBeInTheDocument();
    expect(screen.getByRole('searchbox')).toHaveValue('yok');
  });

  it('arama sunucu hatasında yeniden denenebilir hata gösterir', async () => {
    mockList(() => []);
    server.use(
      http.get(apiUrl('/knowledge/search'), () =>
        HttpResponse.json({ detail: 'Arama motoru hatası' }, { status: 500 }),
      ),
    );
    render(AGENT, '/bilgi-bankasi?q=vpn');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Arama yapılamadı')).toBeInTheDocument();
    expect(within(alert).getByText('Arama motoru hatası')).toBeInTheDocument();
    expect(within(alert).getByRole('button', { name: 'Yeniden dene' })).toBeInTheDocument();
  });

  it('liste yüklenemezse hata gösterir', async () => {
    mockList(() => HttpResponse.json({ detail: 'Bu işlem için yetkiniz yok.' }, { status: 403 }));
    render();
    expect(await screen.findByRole('heading', { name: 'Yetkiniz yok' })).toBeInTheDocument();
  });

  it('onaylayıcı makale oluşturamaz (salt okunur)', async () => {
    mockList(() => [makeArticle()]);
    render(APPROVER);
    await screen.findAllByTestId('article-row');
    expect(screen.queryByRole('button', { name: 'Yeni makale' })).not.toBeInTheDocument();
  });

  it('yeni makale: doğrulama, taslak kaydı ve makale sayfasına yönlendirme', async () => {
    mockList(() => []);
    let posted: ArticleCreate | null = null;
    const created = makeArticle({
      id: 'new-1',
      number: 'KB0000099',
      title: 'Yazıcı kuyruğu takılıyor',
      body: 'Satır 1\nSatır 2',
      state: 'draft',
      is_known_error: true,
      workaround: 'Spooler servisini yeniden başlatın.',
      category: 'yazici',
    });
    server.use(
      http.post(apiUrl('/knowledge/articles'), async ({ request }) => {
        posted = (await request.json()) as ArticleCreate;
        return HttpResponse.json(created, { status: 201 });
      }),
      http.get(apiUrl('/knowledge/articles/new-1'), () => HttpResponse.json(created)),
    );
    const { user } = renderApp({ user: AGENT, route: '/bilgi-bankasi' });
    await user.click(await screen.findByRole('button', { name: 'Yeni makale' }));
    const dialog = await screen.findByRole('dialog', { name: 'Yeni makale' });

    await user.click(within(dialog).getByRole('button', { name: 'Taslak olarak kaydet' }));
    expect(within(dialog).getByText('Başlık zorunludur.')).toBeInTheDocument();
    expect(within(dialog).getByText('İçerik zorunludur.')).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Başlık/)).toHaveFocus();
    expect(posted).toBeNull();

    await user.type(within(dialog).getByLabelText(/Başlık/), 'Yazıcı kuyruğu takılıyor');
    await user.selectOptions(within(dialog).getByLabelText('Kategori'), 'Yazıcı ve tarayıcı');
    await user.type(within(dialog).getByLabelText(/İçerik/), 'Satır 1{Enter}Satır 2');
    await user.click(within(dialog).getByRole('checkbox', { name: /Bilinen hata/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Taslak olarak kaydet' }));
    expect(
      within(dialog).getByText('Bilinen hata kayıtları için geçici çözümü yazın.'),
    ).toBeInTheDocument();
    await user.type(
      within(dialog).getByLabelText(/Geçici çözüm/),
      'Spooler servisini yeniden başlatın.',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Taslak olarak kaydet' }));

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Yazıcı kuyruğu takılıyor' }),
    ).toBeVisible();
    expect(posted).toEqual({
      title: 'Yazıcı kuyruğu takılıyor',
      body: 'Satır 1\nSatır 2',
      category: 'yazici',
      is_known_error: true,
      workaround: 'Spooler servisini yeniden başlatın.',
      state: 'draft',
    });
    expect(await screen.findByText('Taslak kaydedildi')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('yeni makale: "Kaydet ve yayınla" ve sunucu doğrulama hatası', async () => {
    mockList(() => []);
    const bodies: ArticleCreate[] = [];
    server.use(
      http.post(apiUrl('/knowledge/articles'), async ({ request }) => {
        bodies.push((await request.json()) as ArticleCreate);
        return HttpResponse.json(
          { detail: [{ loc: ['body', 'title'], msg: 'Başlık zaten kullanımda' }] },
          { status: 422 },
        );
      }),
    );
    const { user } = render();
    await user.click(await screen.findByRole('button', { name: 'Yeni makale' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Başlık/), 'VPN');
    await user.type(within(dialog).getByLabelText(/İçerik/), 'Metin');
    await user.click(within(dialog).getByRole('button', { name: 'Kaydet ve yayınla' }));
    expect(await within(dialog).findByText('Başlık zaten kullanımda')).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Başlık/)).toHaveAttribute('aria-invalid', 'true');
    expect(bodies[0]?.state).toBe('published');
  });

  it('yeni makale: ağ/sunucu hatası formda gösterilir', async () => {
    mockList(() => []);
    server.use(
      http.post(apiUrl('/knowledge/articles'), () =>
        HttpResponse.json({ detail: 'Sunucu hatası' }, { status: 500 }),
      ),
    );
    const { user } = render();
    await user.click(await screen.findByRole('button', { name: 'Yeni makale' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Başlık/), 'VPN');
    await user.type(within(dialog).getByLabelText(/İçerik/), 'Metin');
    await user.click(within(dialog).getByRole('button', { name: 'Taslak olarak kaydet' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Sunucu hatası');
  });
});
