import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { errorMessage, isApiError } from '@/api';
import { useAuth } from '@/auth/useAuth';
import { Button, ConfirmDialog, ErrorState, Spinner, useToast } from '@/components';
import { formatDateTime } from '@/lib/format';
import { usePageTitle } from '@/lib/usePageTitle';
import { ArticleFormDialog } from './components/ArticleFormDialog';
import { ArticleStateBadge, KnownErrorBadge } from './components/ArticleBadges';
import { toUpdateBody } from './form';
import { useArticle, useArticleTransition, useUpdateArticle, type Transition } from './hooks';
import { categoryLabel } from './labels';
import styles from './Knowledge.module.css';

const BackLink = () => (
  <Link to="/bilgi-bankasi" className={styles.back}>
    ← Bilgi bankası
  </Link>
);

/** /bilgi-bankasi/:id — makale ayrıntısı, düzenleme, yayınlama/arşivleme */
export default function KnowledgeArticlePage() {
  const { id = '' } = useParams();
  const { hasRole } = useAuth();
  const canEdit = hasRole('agent');
  const toast = useToast();
  const query = useArticle(id);
  const update = useUpdateArticle(id);
  const transition = useArticleTransition(id);
  const [editing, setEditing] = useState(false);
  const [confirmRetire, setConfirmRetire] = useState(false);
  usePageTitle(query.data?.title ?? 'Makale');

  if (query.isPending) return <Spinner block label="Makale yükleniyor…" />;
  if (query.isError) {
    const notFound = isApiError(query.error) && query.error.isNotFound;
    return (
      <div className="page">
        <BackLink />
        <h1>{notFound ? 'Makale bulunamadı' : 'Makale'}</h1>
        <ErrorState
          error={query.error}
          title={notFound ? 'Bulunamadı' : undefined}
          message={notFound ? 'Makale silinmiş ya da adres hatalı olabilir.' : undefined}
          onRetry={notFound ? undefined : () => void query.refetch()}
        />
      </div>
    );
  }

  const article = query.data;
  const canPublish = article.state === 'draft' || article.state === 'retired';
  const canRetire = article.state === 'draft' || article.state === 'published';

  const runTransition = (t: Transition) => {
    transition.mutate(t, {
      onSuccess: (a) => {
        setConfirmRetire(false);
        toast.show({
          tone: 'success',
          title: t === 'publish' ? 'Makale yayınlandı' : 'Makale arşive kaldırıldı',
          description: `${a.number} · ${a.title}`,
        });
      },
      onError: (e) => {
        setConfirmRetire(false);
        const conflict = isApiError(e) && e.isConflict;
        toast.show({
          tone: 'error',
          title: conflict ? 'Makalenin durumu değişmiş' : 'İşlem tamamlanamadı',
          description: conflict ? `${errorMessage(e)} Güncel hâli yüklendi.` : errorMessage(e),
        });
        if (conflict) void query.refetch();
      },
    });
  };

  return (
    <div className="page">
      <BackLink />
      <header className={styles.articleHeader}>
        <div className="stack">
          <div className="row">
            <span className={styles.number}>{article.number}</span>
            <ArticleStateBadge state={article.state} />
            {article.is_known_error && <KnownErrorBadge />}
          </div>
          <h1 className={styles.articleTitle}>{article.title}</h1>
        </div>
        {canEdit && (
          <div className={styles.actions} role="group" aria-label="Makale işlemleri">
            <Button
              variant="secondary"
              onClick={() => {
                update.reset();
                setEditing(true);
              }}
            >
              Düzenle
            </Button>
            {canPublish && (
              <Button
                loading={transition.isPending && transition.variables === 'publish'}
                disabled={transition.isPending}
                onClick={() => {
                  runTransition('publish');
                }}
              >
                {article.state === 'retired' ? 'Yeniden yayınla' : 'Yayınla'}
              </Button>
            )}
            {canRetire && (
              <Button
                variant="danger"
                disabled={transition.isPending}
                onClick={() => {
                  setConfirmRetire(true);
                }}
              >
                Arşive kaldır
              </Button>
            )}
          </div>
        )}
      </header>

      {article.state !== 'published' && (
        <p className={styles.notice} role="note">
          {article.state === 'draft'
            ? 'Bu makale taslak: aramada ve yapay zekâ önerilerinde kullanılmaz.'
            : 'Bu makale arşivde: aramada ve yapay zekâ önerilerinde kullanılmaz.'}
        </p>
      )}

      <dl className={styles.meta}>
        <div>
          <dt>Kategori</dt>
          <dd>{categoryLabel(article.category)}</dd>
        </div>
        <div>
          <dt>Oluşturulma</dt>
          <dd>{formatDateTime(article.created_at)}</dd>
        </div>
        <div>
          <dt>Son güncelleme</dt>
          <dd>{formatDateTime(article.updated_at)}</dd>
        </div>
        {article.valid_until && (
          <div>
            <dt>Geçerlilik sonu</dt>
            <dd>{formatDateTime(article.valid_until)}</dd>
          </div>
        )}
      </dl>

      <section className="card stack" aria-labelledby="kb-body-title">
        <h2 id="kb-body-title" className={styles.sectionTitle}>
          İçerik
        </h2>
        <div className={`prewrap ${styles.body}`} data-testid="article-body">
          {article.body}
        </div>
      </section>

      {article.is_known_error && (
        <section className={`card stack ${styles.workaround}`} aria-labelledby="kb-wa-title">
          <h2 id="kb-wa-title" className={styles.sectionTitle}>
            Geçici çözüm
          </h2>
          {article.workaround ? (
            <div className={`prewrap ${styles.body}`}>{article.workaround}</div>
          ) : (
            <p className="muted">Bu bilinen hata için henüz geçici çözüm yazılmamış.</p>
          )}
        </section>
      )}

      {article.source_incident_ids.length > 0 && (
        <section className="stack" aria-labelledby="kb-src-title">
          <h2 id="kb-src-title" className={styles.sectionTitle}>
            Kaynak kayıtlar
          </h2>
          <ul className={styles.sources}>
            {article.source_incident_ids.map((sid) => (
              <li key={sid}>
                <Link to={`/kayitlar/${sid}`}>Kayıt {sid}</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {canEdit && (
        <>
          <ArticleFormDialog
            open={editing}
            article={article}
            busy={update.isPending}
            error={update.error}
            onClose={() => {
              setEditing(false);
            }}
            onSubmit={(values) => {
              const patch = toUpdateBody(article, values);
              if (Object.keys(patch).length === 0) {
                setEditing(false);
                toast.show({ tone: 'info', title: 'Değişiklik yapılmadı' });
                return;
              }
              update.mutate(patch, {
                onSuccess: () => {
                  setEditing(false);
                  toast.show({ tone: 'success', title: 'Makale güncellendi' });
                },
              });
            }}
          />
          <ConfirmDialog
            open={confirmRetire}
            title="Makale arşive kaldırılsın mı?"
            description="Arşivdeki makaleler aramada ve yapay zekâ önerilerinde kullanılmaz. Daha sonra yeniden yayınlayabilirsiniz."
            confirmLabel="Arşive kaldır"
            tone="danger"
            busy={transition.isPending}
            onConfirm={() => {
              runTransition('retire');
            }}
            onCancel={() => {
              setConfirmRetire(false);
            }}
          />
        </>
      )}
    </div>
  );
}
