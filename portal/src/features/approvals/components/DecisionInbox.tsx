import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { decisionsApi, isApiError, queryKeys } from '@/api';
import type { InboxOutcome } from '@/api/types';
import { Button, EmptyState, ErrorState, Spinner } from '@/components';
import { OUTCOME_DESCRIPTIONS } from '@/lib/labels';
import styles from '../Approvals.module.css';
import { DecisionCard } from './DecisionCard';

export const PAGE_SIZE = 20;
const MAX_LIMIT = 200; // backend üst sınırı

const EMPTY_TEXT: Record<InboxOutcome, { title: string; description: string }> = {
  needs_approval: {
    title: 'Onay bekleyen öneri yok',
    description:
      'Riskli yapay zekâ önerileri (ör. P1/P2 değişiklikleri) geldiğinde burada görünür.',
  },
  suggested: {
    title: 'Bekleyen öneri yok',
    description: 'Politikanın otomatik uygulamadığı öneriler burada listelenir.',
  },
  escalated: {
    title: 'İnsana yönlendirilen karar yok',
    description: 'Güveni düşük ya da belirsiz kararlar burada listelenir.',
  },
};

interface DecisionInboxProps {
  outcome: InboxOutcome;
  heading: string;
}

/**
 * Tek bir sonuç türü için onay kutusu. Toplam sayı yok: "Daha fazla göster" limiti büyütür
 * (onaylanan kayıtlar listeden düştüğü için offset sayfalaması yerine).
 */
export function DecisionInbox({ outcome, heading }: DecisionInboxProps) {
  const [limit, setLimit] = useState(PAGE_SIZE);
  const query = { outcome: [outcome], limit };
  const inbox = useQuery({
    queryKey: queryKeys.decisions.inbox(query),
    queryFn: ({ signal }) => decisionsApi.inbox(query, signal),
    placeholderData: keepPreviousData,
  });

  let body;
  if (inbox.isPending) {
    body = <Spinner block label="Öneriler yükleniyor…" />;
  } else if (inbox.isError) {
    const forbidden = isApiError(inbox.error) && inbox.error.isForbidden;
    body = forbidden ? (
      <ErrorState
        error={inbox.error}
        title="Onay kutusuna erişiminiz yok"
        message="Yapay zekâ önerilerini yalnızca onaylayıcı ve yönetici rolleri görebilir. Erişim gerekiyorsa yöneticinize başvurun."
      />
    ) : (
      <ErrorState
        error={inbox.error}
        onRetry={() => {
          void inbox.refetch();
        }}
      />
    );
  } else if (inbox.data.length === 0) {
    body = (
      <EmptyState
        title={EMPTY_TEXT[outcome].title}
        description={EMPTY_TEXT[outcome].description}
        headingLevel={3}
        icon="✓"
      />
    );
  } else {
    const hasMore = inbox.data.length >= limit && limit < MAX_LIMIT;
    body = (
      <>
        <ul className={styles.list} aria-busy={inbox.isFetching || undefined}>
          {inbox.data.map((item) => (
            <DecisionCard key={item.id} item={item} />
          ))}
        </ul>
        {hasMore && (
          <div className={styles.more}>
            <Button
              variant="secondary"
              loading={inbox.isFetching}
              onClick={() => {
                setLimit((l) => Math.min(MAX_LIMIT, l + PAGE_SIZE));
              }}
            >
              Daha fazla göster
            </Button>
          </div>
        )}
      </>
    );
  }

  return (
    <section className="stack" aria-labelledby={`inbox-${outcome}`}>
      <div className={styles.sectionHead}>
        <h2 id={`inbox-${outcome}`} className={styles.sectionHeading}>
          {heading}
          {inbox.data && inbox.data.length > 0 && (
            <span className={styles.count}>
              {' '}
              ({inbox.data.length}
              {inbox.data.length >= limit ? '+' : ''})
            </span>
          )}
        </h2>
        <p className="muted" style={{ margin: 0 }}>
          {OUTCOME_DESCRIPTIONS[outcome]}
        </p>
      </div>
      {body}
    </section>
  );
}
