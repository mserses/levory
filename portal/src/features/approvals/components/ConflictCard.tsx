import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { integrationsApi, isApiError, queryKeys } from '@/api';
import type { SyncEvent } from '@/api/types';
import { Badge, Button, ConfirmDialog, useToast } from '@/components';
import { cx } from '@/lib/cx';
import { formatDateTime, formatRelative } from '@/lib/format';
import styles from '../Approvals.module.css';
import {
  buildConflictRows,
  displayValue,
  fieldLabel,
  parseConflictPayload,
  systemLabel,
} from '../proposal';

type Winner = 'local' | 'remote';

interface ConflictCardProps {
  event: SyncEvent;
}

/** Bir senkron çakışması: yerel ↔ uzak alanlar yan yana, kazanan tarafı seçme. */
export function ConflictCard({ event }: ConflictCardProps) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [pending, setPending] = useState<Winner | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const parsed = parseConflictPayload(event.payload);
  const rows = buildConflictRows(parsed);
  const diffCount = rows.filter((r) => r.differs).length;
  const system = systemLabel(event.system);
  const remoteRef = parsed.remote.number ?? parsed.remote.external_id ?? null;
  const incidentPath = parsed.incidentId
    ? `/kayitlar/${encodeURIComponent(parsed.incidentId)}`
    : null;
  const headingId = `c-${event.id}`;

  const resolve = useMutation({
    mutationFn: (winner: Winner) => integrationsApi.resolveConflict(event.id, winner),
    onSuccess: async (_result, winner) => {
      setPending(null);
      toast.show({
        tone: 'success',
        title: 'Çakışma çözüldü',
        description:
          winner === 'local'
            ? `ServisZeka kaydı ${system} sistemine gönderildi.`
            : `${system} kaydı ServisZeka'ya alındı.`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.integrations.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.incidents.all }),
      ]);
    },
    onError: async (error) => {
      setPending(null);
      if (isApiError(error) && error.isNotFound) {
        toast.show({
          tone: 'info',
          title: 'Çakışma zaten çözülmüş',
          description: 'Liste yenilendi.',
        });
        await queryClient.invalidateQueries({ queryKey: queryKeys.integrations.conflicts });
        return;
      }
      if (isApiError(error) && error.isForbidden) {
        setProblem('Bu çakışmayı çözme yetkiniz yok.');
        return;
      }
      if (isApiError(error) && error.status === 502) {
        setProblem(
          `${system} sistemine ulaşılamadı: ${error.detail}. Biraz sonra yeniden deneyin.`,
        );
        return;
      }
      setProblem(isApiError(error) ? error.detail : 'Beklenmeyen bir hata oluştu.');
    },
  });

  return (
    <li className={styles.item} data-testid="conflict-item">
      <article className={styles.itemCard} aria-labelledby={headingId}>
        <header className={styles.itemHead}>
          <div className={styles.itemTitle}>
            <h3 id={headingId} className={styles.itemHeading}>
              {parsed.local.title ?? 'Kayıt'} <span className="muted">· {system}</span>
            </h3>
            <p className={styles.itemMeta}>
              {formatRelative(event.created_at)} ({formatDateTime(event.created_at)})
              {remoteRef && ` · Uzak kayıt ${remoteRef}`}
            </p>
          </div>
          <Badge tone={diffCount > 0 ? 'warning' : 'neutral'}>
            {diffCount > 0 ? `${diffCount} alan farklı` : 'Alan farkı yok'}
          </Badge>
        </header>

        <p className="prewrap" style={{ margin: 0 }}>
          {event.message}
        </p>

        <dl className={styles.facts}>
          <div>
            <dt>Yerel sürüm</dt>
            <dd>
              {parsed.localVersion ?? '—'}
              {parsed.lastSyncedLocalVersion !== null &&
                ` (son eşitleme: ${parsed.lastSyncedLocalVersion})`}
            </dd>
          </div>
          <div>
            <dt>Uzak güncelleme</dt>
            <dd>
              {formatDateTime(parsed.remoteUpdatedAt, parsed.remoteUpdatedAt ?? '—')}
              {parsed.lastSyncedRemoteUpdated &&
                ` (son eşitleme: ${formatDateTime(parsed.lastSyncedRemoteUpdated, parsed.lastSyncedRemoteUpdated)})`}
            </dd>
          </div>
        </dl>

        {rows.length > 0 ? (
          <div className={styles.diffWrap}>
            <table className={styles.diff}>
              <caption className={styles.diffCaption}>Alan karşılaştırması</caption>
              <thead>
                <tr>
                  <th scope="col">Alan</th>
                  <th scope="col">Yerel (ServisZeka)</th>
                  <th scope="col">Uzak ({system})</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.field}
                    className={cx(row.differs && styles.rowChanged)}
                    data-differs={row.differs || undefined}
                  >
                    <th scope="row">
                      {fieldLabel(row.field)}
                      {row.differs && (
                        <>
                          {' '}
                          <Badge tone="warning">Farklı</Badge>
                        </>
                      )}
                    </th>
                    <td data-label="Yerel (ServisZeka)" className="prewrap">
                      {row.localMissing ? (
                        <span className="muted">Alan yok</span>
                      ) : (
                        displayValue(row.field, row.local)
                      )}
                    </td>
                    <td data-label={`Uzak (${system})`} className="prewrap">
                      {row.remoteMissing ? (
                        <span className="muted">Alan yok</span>
                      ) : (
                        displayValue(row.field, row.remote)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Çakışma kaydında alan ayrıntısı yok.</p>
        )}

        {problem && (
          <div className={styles.problem} role="alert">
            <p className={styles.problemTitle}>Çakışma çözülemedi.</p>
            <p className={styles.noticeText}>{problem}</p>
          </div>
        )}

        <div className={styles.actionsRow}>
          <Button
            variant="secondary"
            onClick={() => {
              setProblem(null);
              setPending('local');
            }}
            disabled={resolve.isPending}
            aria-describedby={headingId}
          >
            Yerel kaydı koru
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              setProblem(null);
              setPending('remote');
            }}
            disabled={resolve.isPending}
            aria-describedby={headingId}
          >
            Uzak kaydı al
          </Button>
          {incidentPath && (
            <Link to={incidentPath} className={styles.inlineLink}>
              Kaydı incele
            </Link>
          )}
        </div>
      </article>

      <ConfirmDialog
        open={pending !== null}
        title={pending === 'local' ? 'Yerel kayıt korunsun mu?' : 'Uzak kayıt alınsın mı?'}
        description={
          pending === 'local'
            ? `ServisZeka'daki değerler ${system} kaydının üzerine yazılır.`
            : `${system} kaydındaki değerler ServisZeka kaydının üzerine yazılır.`
        }
        confirmLabel={pending === 'local' ? 'Yerel kaydı koru' : 'Uzak kaydı al'}
        tone="danger"
        busy={resolve.isPending}
        onConfirm={() => {
          if (pending) resolve.mutate(pending);
        }}
        onCancel={() => {
          setPending(null);
        }}
      >
        {diffCount > 0 ? (
          <ul className={styles.changeList}>
            {rows
              .filter((r) => r.differs)
              .map((r) => (
                <li key={r.field}>
                  <strong>{fieldLabel(r.field)}:</strong>{' '}
                  {pending === 'local'
                    ? `${displayValue(r.field, r.remote)} → ${displayValue(r.field, r.local)}`
                    : `${displayValue(r.field, r.local)} → ${displayValue(r.field, r.remote)}`}
                </li>
              ))}
          </ul>
        ) : (
          <p style={{ margin: 0 }}>Karşılaştırılabilir alan farkı görünmüyor.</p>
        )}
      </ConfirmDialog>
    </li>
  );
}
