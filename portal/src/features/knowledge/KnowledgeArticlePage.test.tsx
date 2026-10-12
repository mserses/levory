import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { Article, ArticleUpdate } from '@/api/types';
import { makeArticle } from '@/test/fixtures';
import { apiUrl } from '@/test/handlers';
import { AGENT, APPROVER, renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import KnowledgeArticlePage from './KnowledgeArticlePage';

function mockArticle(initial: Article) {
  const state = { article: initial, gets: 0 };
  server.use(
    http.get(apiUrl(`/knowledge/articles/${initial.id}`), () => {
      state.gets++;
      return HttpResponse.json(state.article);
    }),
  );
  return state;
}

const render = (article: Article, user = AGENT) =>
  renderWithProviders(<KnowledgeArticlePage />, {
    user,
    route: `/bilgi-bankasi/${article.id}`,
    path: '/bilgi-bankasi/:id',
  });

describe('KnowledgeArticlePage', () => {
  it('makaleyi düz metin olarak, satır sonlarını koruyarak gösterir', async () => {
    const article = makeArticle({
      title: 'Outlook açılırken donuyor',
      body: 'Adım 1: Outlook kapatın.\nAdım 2: <script>alert(1)</script> profili onarın.',
      category: 'eposta_isbirligi',
      is_known_error: true,
      workaround: 'Web üzerinden Outlook kullanın.',
      source_incident_ids: ['inc-42'],
      state: 'published',
    });
    mockArticle(article);
    const { container } = render(article);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Outlook açılırken donuyor' }),
    ).toBeVisible();
    const body = screen.getByTestId('article-body');
    expect(body.textContent).toBe(article.body);
    expect(body).toHaveClass('prewrap');
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('E-posta ve iş birliği')).toBeInTheDocument();
    expect(screen.getByText('Bilinen hata')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Geçici çözüm' })).toBeInTheDocument();
    expect(screen.getByText('Web üzerinden Outlook kullanın.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Kayıt inc-42' })).toHaveAttribute(
      'href',
      '/kayitlar/inc-42',
    );
    expect(screen.queryByRole('button', { name: 'Yayınla' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Arşive kaldır' })).toBeInTheDocument();
    expect(document.title).toBe('Outlook açılırken donuyor · ServisZeka');
  });

  it('taslağı yayınlar', async () => {
    const article = makeArticle({ state: 'draft' });
    const mock = mockArticle(article);
    server.use(
      http.post(apiUrl(`/knowledge/articles/${article.id}/publish`), () => {
        mock.article = { ...article, state: 'published' };
        return HttpResponse.json(mock.article);
      }),
    );
    const { user } = render(article);
    expect(await screen.findByText(/Bu makale taslak/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Yayınla' }));
    expect(await screen.findByText('Makale yayınlandı')).toBeInTheDocument();
    expect(screen.getByText('Yayında')).toBeInTheDocument();
    expect(screen.queryByText(/Bu makale taslak/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Yayınla' })).not.toBeInTheDocument();
  });

  it('onay sonrası arşive kaldırır; arşivdeki makale yeniden yayınlanabilir', async () => {
    const article = makeArticle({ state: 'published' });
    mockArticle(article);
    let retired = 0;
    server.use(
      http.post(apiUrl(`/knowledge/articles/${article.id}/retire`), () => {
        retired++;
        return HttpResponse.json({ ...article, state: 'retired' });
      }),
    );
    const { user } = render(article);
    await user.click(await screen.findByRole('button', { name: 'Arşive kaldır' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Makale arşive kaldırılsın mı?' });
    await user.click(within(dialog).getByRole('button', { name: 'Vazgeç' }));
    expect(retired).toBe(0);

    await user.click(screen.getByRole('button', { name: 'Arşive kaldır' }));
    await user.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Arşive kaldır' }),
    );
    expect(await screen.findByText('Makale arşive kaldırıldı')).toBeInTheDocument();
    expect(retired).toBe(1);
    expect(screen.getByText('Arşivde')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Yeniden yayınla' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Arşive kaldır' })).not.toBeInTheDocument();
  });

  it('durum çakışmasında (409) hata bildirir ve makaleyi yeniden yükler', async () => {
    const article = makeArticle({ state: 'draft' });
    const mock = mockArticle(article);
    server.use(
      http.post(apiUrl(`/knowledge/articles/${article.id}/publish`), () => {
        mock.article = { ...article, state: 'published' };
        return HttpResponse.json(
          { detail: 'published -> published geçişine izin verilmez' },
          { status: 409 },
        );
      }),
    );
    const { user } = render(article);
    await user.click(await screen.findByRole('button', { name: 'Yayınla' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Makalenin durumu değişmiş');
    expect(alert).toHaveTextContent('Güncel hâli yüklendi.');
    await waitFor(() => {
      expect(screen.getByText('Yayında')).toBeInTheDocument();
    });
    expect(mock.gets).toBe(2);
  });

  it('düzenler: yalnızca değişen alanları gönderir', async () => {
    const article = makeArticle({ title: 'Eski başlık', body: 'Gövde', category: 'ag' });
    const mock = mockArticle(article);
    let patch: ArticleUpdate | null = null;
    server.use(
      http.patch(apiUrl(`/knowledge/articles/${article.id}`), async ({ request }) => {
        patch = (await request.json()) as ArticleUpdate;
        mock.article = { ...article, title: 'Yeni başlık', category: null };
        return HttpResponse.json(mock.article);
      }),
    );
    const { user } = render(article);
    await user.click(await screen.findByRole('button', { name: 'Düzenle' }));
    const dialog = screen.getByRole('dialog', { name: 'Makaleyi düzenle' });
    const title = within(dialog).getByLabelText(/Başlık/);
    expect(title).toHaveValue('Eski başlık');
    await user.clear(title);
    await user.type(title, 'Yeni başlık');
    await user.selectOptions(within(dialog).getByLabelText('Kategori'), 'Kategorisiz');
    await user.click(within(dialog).getByRole('button', { name: 'Kaydet' }));

    expect(await screen.findByText('Makale güncellendi')).toBeInTheDocument();
    expect(patch).toEqual({ title: 'Yeni başlık', category: null });
    expect(screen.getByRole('heading', { level: 1, name: 'Yeni başlık' })).toBeInTheDocument();
    expect(screen.getByText('Kategorisiz')).toBeInTheDocument();
  });

  it('değişiklik yoksa istek göndermez', async () => {
    const article = makeArticle();
    mockArticle(article);
    const { user } = render(article);
    await user.click(await screen.findByRole('button', { name: 'Düzenle' }));
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(await screen.findByText('Değişiklik yapılmadı')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('bulunamayan makale için 404 durumu', async () => {
    server.use(
      http.get(apiUrl('/knowledge/articles/yok'), () =>
        HttpResponse.json({ detail: 'Makale bulunamadı' }, { status: 404 }),
      ),
    );
    renderWithProviders(<KnowledgeArticlePage />, {
      route: '/bilgi-bankasi/yok',
      path: '/bilgi-bankasi/:id',
    });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Makale bulunamadı' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '← Bilgi bankası' })).toHaveAttribute(
      'href',
      '/bilgi-bankasi',
    );
    expect(screen.queryByRole('button', { name: 'Yeniden dene' })).not.toBeInTheDocument();
  });

  it('onaylayıcı makaleyi yalnızca okur', async () => {
    const article = makeArticle({ state: 'draft' });
    mockArticle(article);
    render(article, APPROVER);
    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByRole('button', { name: 'Düzenle' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Yayınla' })).not.toBeInTheDocument();
  });
});
