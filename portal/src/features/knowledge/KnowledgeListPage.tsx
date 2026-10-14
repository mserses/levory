import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { Button, EmptyState, ErrorState, Select, Spinner, TextInput, useToast } from '@/components';
import { formatDateTime, formatRelative } from '@/lib/format';
import { usePageTitle } from '@/lib/usePageTitle';
import { ArticleFormDialog } from './components/ArticleFormDialog';
import { ArticleStateBadge, KnownErrorBadge } from './components/ArticleBadges';
import { Highlighted } from './components/Highlighted';
import { toCreateBody } from './form';
import { useArticleList, useArticleSearch, useCreateArticle } from './hooks';
import { ARTICLE_STATE_LABELS, ARTICLE_STATES, categoryLabel, isArticleState } from './labels';
import styles from './Knowledge.module.css';
import { cx } from '@/lib/cx';

const STATE_OPTIONS = ARTICLE_STATES.map((value) => ({
  value,
  label: ARTICLE_STATE_LABELS[value],
}));

function SearchResults({ q }: { q: string }) {
  const search = useArticleSearch(q);
  if (search.isPending) return <Spinner block label="Aranıyor…" />;
  if (search.isError)
    return (
      <ErrorState
        error={search.error}
        title="Arama yapılamadı"
        onRetry={() => void search.refetch()}
      />
    );
  const hits = search.data.hits;
  return (
    <>
      <p className={styles.resultCount} aria-live="polite">
        “{q}” için {hits.length} sonuç
      </p>
      {hits.length === 0 ? (
        <EmptyState
          headingLevel={3}
          icon="⌕"
          title="Eşleşen makale yok"
          description="Farklı ya da daha genel kelimeler deneyin. Arama yalnızca yayındaki makalelerde yapılır."
        />
      ) : (
        <ol className={styles.hits}>
          {hits.map((hit) => (
            <li key={hit.article_id} className={styles.hit} data-testid="search-hit">
              <div className={styles.hitHead}>
                <Link to={`/bilgi-bankasi/${hit.article_id}`} className={styles.hitTitle}>
                  <Highlighted text={hit.title} query={q} />
                </Link>
                <span className={styles.number}>{hit.number}</span>
              </div>
              <p className={styles.snippet}>
                <Highlighted text={hit.snippet} query={q} />
              </p>
              <p className={styles.score}>İlgi puanı: {hit.score.toFixed(2)}</p>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function SearchForm({ initial, onSearch }: { initial: string; onSearch: (q: string) => void }) {
  const [text, setText] = useState(initial);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSearch(text.trim());
  };
  return (
    <form role="search" className={styles.searchForm} onSubmit={submit}>
      <TextInput
        type="search"
        label="Makalelerde ara"
        hint="Başlık, içerik ve geçici çözümlerde arar (yalnızca yayındaki makaleler)."
        value={text}
        onChange={(e) => {
          setText(e.target.value);
        }}
        className={styles.searchField}
        autoComplete="off"
      />
      <div className={styles.searchActions}>
        <Button type="submit">Ara</Button>
        {initial && (
          <Button
            variant="ghost"
            onClick={() => {
              setText('');
              onSearch('');
            }}
          >
            Aramayı temizle
          </Button>
        )}
      </div>
    </form>
  );
}

/** /bilgi-bankasi — makale arama, liste (durum filtresi) ve yeni makale */
export default function KnowledgeListPage() {
  usePageTitle('Bilgi bankası');
  const { hasRole } = useAuth();
  const canEdit = hasRole('agent');
  const toast = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const q = params.get('q')?.trim() ?? '';
  const rawState = params.get('durum') ?? '';
  const state = isArticleState(rawState) ? rawState : null;
  const page = Math.max(1, Number.parseInt(params.get('sayfa') ?? '1', 10) || 1);

  const list = useArticleList(state, page);
  const create = useCreateArticle();
  const [creating, setCreating] = useState(false);

  const updateParams = (patch: Record<string, string | null>) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v);
        else next.delete(k);
      }
      return next;
    });
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Bilgi bankası</h1>
          <p className="muted">
            Çözüm makaleleri ve bilinen hatalar. Yapay zekâ önerileri kaynak olarak yayındaki
            makaleleri gösterir.
          </p>
        </div>
        {canEdit && (
          <Button
            onClick={() => {
              create.reset();
              setCreating(true);
            }}
          >
            Yeni makale
          </Button>
        )}
      </div>

      <section className="card stack" aria-labelledby="kb-search-title">
        <h2 id="kb-search-title" className={styles.sectionTitle}>
          Arama
        </h2>
        <SearchForm
          key={q}
          initial={q}
          onSearch={(term) => {
            updateParams({ q: term || null });
          }}
        />
        {q && <SearchResults q={q} />}
      </section>

      <section className="stack" aria-labelledby="kb-list-title">
        <div className={styles.listHead}>
          <h2 id="kb-list-title" className={styles.sectionTitle}>
            Makaleler
          </h2>
          <Select
            label="Durum"
            className={styles.stateFilter}
            placeholder="Tümü"
            options={STATE_OPTIONS}
            value={state ?? ''}
            onChange={(e) => {
              updateParams({ durum: e.target.value || null, sayfa: null });
            }}
          />
        </div>

        {list.isPending ? (
          <Spinner block label="Makaleler yükleniyor…" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.articles.length === 0 ? (
          <EmptyState
            title={page > 1 ? 'Bu sayfada makale yok' : 'Makale bulunamadı'}
            description={
              state
                ? `“${ARTICLE_STATE_LABELS[state]}” durumunda makale yok.`
                : 'Henüz bilgi bankası makalesi oluşturulmamış.'
            }
          />
        ) : (
          <div className={cx(styles.tableWrap, styles.listWrap)}>
            <table className={styles.table}>
              <caption className="visually-hidden">Bilgi bankası makaleleri, sayfa {page}</caption>
              <thead>
                <tr>
                  <th scope="col">Makale</th>
                  <th scope="col">Kategori</th>
                  <th scope="col">Durum</th>
                  <th scope="col">Güncelleme</th>
                </tr>
              </thead>
              <tbody>
                {list.articles.map((a) => (
                  <tr key={a.id} data-testid="article-row">
                    <td className={styles.titleCell}>
                      <div className={styles.titleInner}>
                        <Link to={`/bilgi-bankasi/${a.id}`}>
                          <span className={styles.number}>{a.number}</span>
                          <span className={styles.title}>{a.title}</span>
                        </Link>
                        {a.is_known_error && <KnownErrorBadge />}
                      </div>
                    </td>
                    <td data-label="Kategori">{categoryLabel(a.category)}</td>
                    <td data-label="Durum">
                      <ArticleStateBadge state={a.state} />
                    </td>
                    <td data-label="Güncelleme">
                      <time dateTime={a.updated_at} title={formatDateTime(a.updated_at)}>
                        {formatRelative(a.updated_at)}
                      </time>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {list.isSuccess && (page > 1 || list.hasNext) && (
          <nav className={styles.pager} aria-label="Sayfalama">
            <Button
              variant="secondary"
              size="sm"
              disabled={page <= 1}
              onClick={() => {
                updateParams({ sayfa: page - 1 > 1 ? String(page - 1) : null });
              }}
            >
              ← Önceki
            </Button>
            <span aria-current="page">Sayfa {page}</span>
            <Button
              variant="secondary"
              size="sm"
              disabled={!list.hasNext}
              onClick={() => {
                updateParams({ sayfa: String(page + 1) });
              }}
            >
              Sonraki →
            </Button>
          </nav>
        )}
      </section>

      {canEdit && (
        <ArticleFormDialog
          open={creating}
          busy={create.isPending}
          error={create.error}
          onClose={() => {
            setCreating(false);
          }}
          onSubmit={(values, publish) => {
            create.mutate(toCreateBody(values, publish), {
              onSuccess: (article) => {
                setCreating(false);
                toast.show({
                  tone: 'success',
                  title: publish ? 'Makale yayınlandı' : 'Taslak kaydedildi',
                  description: `${article.number} · ${article.title}`,
                });
                navigate(`/bilgi-bankasi/${article.id}`);
              },
            });
          }}
        />
      )}
    </div>
  );
}
