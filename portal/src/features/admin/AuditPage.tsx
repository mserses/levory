import { useMutation, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { auditApi, errorMessage, queryKeys } from '@/api';
import type { AuditEntry } from '@/api/types';
import { Button, EmptyState, ErrorState, Spinner } from '@/components';
import { formatDateTime } from '@/lib/format';
import { usePageTitle } from '@/lib/usePageTitle';
import { AUDIT_ACTION_LABELS, ENTITY_TYPE_LABELS } from './labels';
import styles from './Admin.module.css';

const AUDIT_PAGE_SIZE = 25;

const shortHash = (h: string) => (h.length > 12 ? `${h.slice(0, 12)}…` : h);

function VerifyPanel() {
  const verify = useMutation({ mutationFn: () => auditApi.verify() });
  const checkedAt = verify.isSuccess || verify.isError ? new Date(verify.submittedAt) : null;

  return (
    <section className="card stack" aria-labelledby="audit-verify-title">
      <div className={styles.cardHead}>
        <div>
          <h2 id="audit-verify-title" className={styles.sectionTitle}>
            Zincir doğrulama
          </h2>
          <p className="muted">
            Her denetim kaydı bir öncekinin hash değerini içerir. Doğrulama tüm zinciri baştan
            yeniden hesaplar; silinen ya da değiştirilen kayıt zinciri bozar.
          </p>
        </div>
        <Button
          loading={verify.isPending}
          onClick={() => {
            verify.mutate();
          }}
        >
          {verify.isIdle ? 'Zinciri doğrula' : 'Yeniden doğrula'}
        </Button>
      </div>
      <div role="status" aria-live="polite" data-testid="verify-status">
        {verify.isPending && <p className="muted">Zincir doğrulanıyor…</p>}
        {verify.isSuccess &&
          (verify.data.valid ? (
            <div className={`${styles.result} ${styles.valid}`}>
              <span className={styles.resultIcon} aria-hidden="true">
                ✓
              </span>
              <div>
                <p className={styles.resultTitle}>Zincir geçerli</p>
                <p className={styles.resultText}>
                  Tüm denetim kayıtlarının hash zinciri bütün; oynama tespit edilmedi.
                  {checkedAt && ` Denetlenme: ${formatDateTime(checkedAt)}.`}
                </p>
              </div>
            </div>
          ) : (
            <div className={`${styles.result} ${styles.invalid}`}>
              <span className={styles.resultIcon} aria-hidden="true">
                ✕
              </span>
              <div>
                <p className={styles.resultTitle}>Zincir BOZUK</p>
                <p className={styles.resultText}>
                  En az bir denetim kaydı değiştirilmiş, silinmiş ya da sırası bozulmuş. Kayıtlara
                  dokunmadan güvenlik ekibini bilgilendirin.
                  {checkedAt && ` Denetlenme: ${formatDateTime(checkedAt)}.`}
                </p>
              </div>
            </div>
          ))}
        {verify.isError && (
          <div className={`${styles.result} ${styles.invalid}`}>
            <span className={styles.resultIcon} aria-hidden="true">
              !
            </span>
            <div>
              <p className={styles.resultTitle}>Doğrulama yapılamadı</p>
              <p className={styles.resultText}>{errorMessage(verify.error)}</p>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function Details({ entry }: { entry: AuditEntry }) {
  const keys = Object.keys(entry.details);
  if (keys.length === 0) return <span className="muted">—</span>;
  return (
    <details className={styles.details}>
      <summary>{keys.length} alan</summary>
      {/* Düz metin JSON; HTML olarak yorumlanmaz */}
      <pre className={styles.json}>{JSON.stringify(entry.details, null, 2)}</pre>
    </details>
  );
}

/** /yonetim/denetim — denetim günlüğü (en yeni önce, sayfalı) ve zincir doğrulama */
export default function AuditPage() {
  usePageTitle('Denetim günlüğü');
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Number.parseInt(params.get('sayfa') ?? '1', 10) || 1);
  const offset = (page - 1) * AUDIT_PAGE_SIZE;
  const list = useQuery({
    queryKey: queryKeys.audit.page(AUDIT_PAGE_SIZE, offset),
    queryFn: ({ signal }) => auditApi.list({ limit: AUDIT_PAGE_SIZE, offset }, signal),
    placeholderData: (prev) => prev,
  });

  const total = list.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / AUDIT_PAGE_SIZE));
  const goTo = (p: number) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (p > 1) next.set('sayfa', String(p));
      else next.delete('sayfa');
      return next;
    });
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Denetim günlüğü</h1>
          <p className="muted">
            Kayıt, yapay zekâ kararı, bilgi bankası ve senkron işlemlerinin değiştirilemez kaydı. En
            yeni kayıt en üstte.
          </p>
        </div>
      </div>

      <VerifyPanel />

      <section className="stack" aria-labelledby="audit-list-title">
        <div className={styles.cardHead}>
          <h2 id="audit-list-title" className={styles.sectionTitle}>
            Kayıtlar
          </h2>
          {list.isSuccess && (
            <p className={styles.count}>Toplam {total.toLocaleString('tr-TR')} kayıt</p>
          )}
        </div>
        {list.isPending ? (
          <Spinner block label="Denetim kayıtları yükleniyor…" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.data.items.length === 0 ? (
          <EmptyState
            title={page > 1 ? 'Bu sayfada kayıt yok' : 'Henüz denetim kaydı yok'}
            action={
              page > 1 ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    goTo(1);
                  }}
                >
                  İlk sayfaya dön
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className={styles.tableWrap} aria-busy={list.isFetching}>
            <table className={styles.table}>
              <caption className="visually-hidden">
                Denetim kayıtları, sayfa {page} / {pages}, en yeni önce
              </caption>
              <thead>
                <tr>
                  <th scope="col">Sıra</th>
                  <th scope="col">Zaman</th>
                  <th scope="col">Kullanıcı</th>
                  <th scope="col">İşlem</th>
                  <th scope="col">Varlık</th>
                  <th scope="col">Ayrıntı</th>
                  <th scope="col">Hash</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((e) => (
                  <tr key={e.seq} data-testid="audit-row">
                    <td data-label="Sıra" className={styles.mono}>
                      #{e.seq}
                    </td>
                    <td data-label="Zaman">
                      <time dateTime={e.occurred_at}>{formatDateTime(e.occurred_at)}</time>
                    </td>
                    <td data-label="Kullanıcı" className={styles.wrap}>
                      {e.actor}
                    </td>
                    <td data-label="İşlem">
                      {AUDIT_ACTION_LABELS[e.action] ?? e.action}
                      <span className={styles.sub}>{e.action}</span>
                    </td>
                    <td data-label="Varlık" className={styles.wrap}>
                      {ENTITY_TYPE_LABELS[e.entity_type] ?? e.entity_type}
                      <span className={styles.sub}>{e.entity_id}</span>
                    </td>
                    <td data-label="Ayrıntı" className={styles.wide}>
                      <Details entry={e} />
                    </td>
                    <td data-label="Hash" className={styles.mono}>
                      <span title={e.hash}>{shortHash(e.hash)}</span>
                      <span className={styles.sub} title={e.prev_hash}>
                        önceki {shortHash(e.prev_hash)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {list.isSuccess && pages > 1 && (
          <nav className={styles.pager} aria-label="Sayfalama">
            <Button
              variant="secondary"
              size="sm"
              disabled={page <= 1}
              onClick={() => {
                goTo(page - 1);
              }}
            >
              ← Daha yeni
            </Button>
            <span aria-current="page">
              Sayfa {page} / {pages}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={page >= pages}
              onClick={() => {
                goTo(page + 1);
              }}
            >
              Daha eski →
            </Button>
          </nav>
        )}
      </section>
    </div>
  );
}
