import { useQuery } from '@tanstack/react-query';
import { integrationsApi, isApiError, queryKeys } from '@/api';
import { EmptyState, ErrorState, Spinner } from '@/components';
import styles from '../Approvals.module.css';
import { ConflictCard } from './ConflictCard';

/** GET /integrations/conflicts — manuel çözüm bekleyen senkron çakışmaları (eskiden yeniye). */
export function SyncConflicts() {
  const conflicts = useQuery({
    queryKey: queryKeys.integrations.conflicts,
    queryFn: ({ signal }) => integrationsApi.conflicts(signal),
  });

  let body;
  if (conflicts.isPending) {
    body = <Spinner block label="Çakışmalar yükleniyor…" />;
  } else if (conflicts.isError) {
    body =
      isApiError(conflicts.error) && conflicts.error.isForbidden ? (
        <ErrorState
          error={conflicts.error}
          title="Senkron çakışmalarına erişiminiz yok"
          message="Çakışmaları yalnızca onaylayıcı ve yönetici rolleri görebilir."
        />
      ) : (
        <ErrorState
          error={conflicts.error}
          onRetry={() => {
            void conflicts.refetch();
          }}
        />
      );
  } else if (conflicts.data.length === 0) {
    body = (
      <EmptyState
        title="Açık senkron çakışması yok"
        description="Bir kayıt hem ServisZeka'da hem harici sistemde değişirse burada çözüm bekler."
        headingLevel={3}
        icon="✓"
      />
    );
  } else {
    body = (
      <ul className={styles.list}>
        {conflicts.data.map((event) => (
          <ConflictCard key={event.id} event={event} />
        ))}
      </ul>
    );
  }

  return (
    <section className="stack" aria-labelledby="sync-conflicts">
      <div className={styles.sectionHead}>
        <h2 id="sync-conflicts" className={styles.sectionHeading}>
          Senkron çakışmaları
          {conflicts.data && conflicts.data.length > 0 && (
            <span className={styles.count}> ({conflicts.data.length})</span>
          )}
        </h2>
        <p className="muted" style={{ margin: 0 }}>
          Kayıt iki tarafta da değişti. Hangi tarafın geçerli olacağını seçin; diğer taraf bu
          değerlerle güncellenir.
        </p>
      </div>
      {body}
    </section>
  );
}
