import { useCallback, useId, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { isApiError } from '@/api/errors';
import type { Incident } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import {
  Button,
  ErrorState,
  PriorityBadge,
  SlaBadge,
  Spinner,
  StatusBadge,
  useToast,
} from '@/components';
import { OUTCOME_DESCRIPTIONS, OUTCOME_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { AIDecisionsPanel } from './components/AIDecisionsPanel';
import { ConflictDialog } from './components/ConflictDialog';
import { IncidentEditForm } from './components/IncidentEditForm';
import { IncidentFields } from './components/IncidentFields';
import { StatusChangeForm } from './components/StatusChangeForm';
import type { SaveFn } from './components/types';
import { useIncident, useIncidentDecisions, useNow, useTriage, useUpdateIncident } from './hooks';
import styles from './IncidentDetailPage.module.css';
import { isFinalStatus } from './rules';

/** /kayitlar/:id — kayıt ayrıntısı, triage ve yapay zekâ kararları (sahibi: incidents) */
export default function IncidentDetailPage() {
  const { id = '' } = useParams();
  const incidentQuery = useIncident(id);
  const incident = incidentQuery.data;
  usePageTitle(incident ? `${incident.number} · ${incident.title}` : 'Kayıt ayrıntısı');

  if (incidentQuery.isPending) return <Spinner block label="Kayıt yükleniyor…" />;
  if (incidentQuery.isError || !incident) {
    const err = incidentQuery.error;
    return (
      <div className="page">
        <BackLink />
        <h1 className="visually-hidden">Kayıt ayrıntısı</h1>
        <ErrorState
          error={err}
          title={isApiError(err) && err.isNotFound ? 'Kayıt bulunamadı' : undefined}
          onRetry={
            isApiError(err) && err.isNotFound ? undefined : () => void incidentQuery.refetch()
          }
        />
      </div>
    );
  }
  return (
    <IncidentDetail
      incident={incident}
      refetch={async () => (await incidentQuery.refetch()).data}
    />
  );
}

function BackLink() {
  return (
    <Link to="/kayitlar" className={styles.back}>
      ← Kayıtlara dön
    </Link>
  );
}

interface ConflictState {
  fields: string[];
}

function IncidentDetail({
  incident,
  refetch,
}: {
  incident: Incident;
  refetch: () => Promise<Incident | undefined>;
}) {
  const { hasRole } = useAuth();
  const canEdit = hasRole('agent'); // PATCH ve triage: agent/admin
  const canApprove = hasRole('approver');
  const toast = useToast();
  const now = useNow();
  const update = useUpdateIncident(incident.id);
  const triage = useTriage(incident.id);
  const decisions = useIncidentDecisions(incident.id);
  const final = isFinalStatus(incident.status);

  // Düzenleme, açıldığı andaki kayıt üzerinden yapılır (sürüm If-Match'e gider)
  const [editBase, setEditBase] = useState<Incident | null>(null);
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  const [reloading, setReloading] = useState(false);
  const [formKey, setFormKey] = useState(0);

  const detailsId = useId();
  const statusId = useId();
  const aiId = useId();

  const save: SaveFn = useCallback(
    async (version, patch, fieldNames) => {
      try {
        await update.mutateAsync({ version, patch });
        toast.show({ title: 'Kayıt güncellendi', tone: 'success' });
        return { ok: true };
      } catch (error) {
        if (isApiError(error) && error.isConflict) {
          setConflict({ fields: fieldNames });
          return { ok: false, conflict: true };
        }
        return { ok: false, conflict: false, error };
      }
    },
    [update, toast],
  );

  const reload = async () => {
    setReloading(true);
    try {
      const fresh = await refetch();
      if (editBase && fresh) setEditBase(fresh);
      setFormKey((k) => k + 1);
      await decisions.refetch();
    } finally {
      setReloading(false);
      setConflict(null);
    }
  };

  const runTriage = () => {
    triage.mutate(undefined, {
      onSuccess: (decision) => {
        toast.show({
          title: `Yapay zekâ kararı: ${OUTCOME_LABELS[decision.outcome]}`,
          description: OUTCOME_DESCRIPTIONS[decision.outcome],
          tone:
            decision.outcome === 'auto_applied'
              ? 'success'
              : decision.outcome === 'rejected'
                ? 'error'
                : decision.outcome === 'suggested'
                  ? 'info'
                  : 'warning',
        });
      },
      onError: (error) => {
        toast.show({
          title: 'Sınıflandırma yapılamadı',
          description: isApiError(error) ? error.detail : undefined,
          tone: 'error',
        });
      },
    });
  };

  return (
    <div className="page">
      <BackLink />
      <header className={styles.header}>
        <p className={styles.number}>{incident.number}</p>
        <h1 className={styles.title}>{incident.title}</h1>
        <div className="row">
          <PriorityBadge priority={incident.priority} />
          <StatusBadge status={incident.status} onHoldReason={incident.on_hold_reason} />
          <SlaBadge incident={incident} now={now} />
        </div>
      </header>

      <div className={styles.layout}>
        <section className="card stack" aria-labelledby={detailsId}>
          <div className={styles.sectionHead}>
            <h2 id={detailsId} className={styles.sectionTitle}>
              {editBase ? 'Kaydı düzenle' : 'Ayrıntılar'}
            </h2>
            {canEdit && !editBase && !final && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setEditBase(incident);
                }}
              >
                Düzenle
              </Button>
            )}
          </div>
          {editBase ? (
            <IncidentEditForm
              key={`${editBase.id}-${editBase.version}-${formKey}`}
              base={editBase}
              onSave={save}
              onCancel={() => {
                setEditBase(null);
              }}
              onSaved={() => {
                setEditBase(null);
              }}
            />
          ) : (
            <IncidentFields incident={incident} now={now} />
          )}
        </section>

        <div className={styles.side}>
          <section className="card stack" aria-labelledby={statusId}>
            <h2 id={statusId} className={styles.sectionTitle}>
              Durum
            </h2>
            {canEdit ? (
              <StatusChangeForm key={formKey} incident={incident} onSave={save} />
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                Durumu yalnızca destek uzmanları değiştirebilir.
              </p>
            )}
          </section>
        </div>
      </div>

      <section className="stack" aria-labelledby={aiId}>
        <div className={styles.sectionHead}>
          <div>
            <h2 id={aiId} className={styles.sectionTitle}>
              Yapay zekâ kararları
            </h2>
            <p className="muted" style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
              Yapay zekâ önerir, insan onaylar. P1/P2'ye yükseltme ve P1/P2'den düşürme her zaman
              onay ister.
            </p>
          </div>
          {canEdit && (
            <Button
              onClick={runTriage}
              loading={triage.isPending}
              disabled={final}
              title={final ? 'Kapalı kayıt sınıflandırılamaz' : undefined}
            >
              Yapay zekâ ile sınıflandır
            </Button>
          )}
        </div>
        {triage.isPending && (
          <p className="muted" role="status" style={{ margin: 0 }}>
            Yapay zekâ kaydı sınıflandırıyor…
          </p>
        )}
        <AIDecisionsPanel
          incident={incident}
          decisions={decisions.data}
          isPending={decisions.isPending}
          error={decisions.isError ? decisions.error : null}
          onRetry={() => void decisions.refetch()}
          canApprove={canApprove}
        />
      </section>

      <ConflictDialog
        open={conflict !== null}
        fields={conflict?.fields ?? []}
        reloading={reloading}
        onReload={() => void reload()}
      />
    </div>
  );
}
