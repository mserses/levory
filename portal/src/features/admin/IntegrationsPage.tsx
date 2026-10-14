import { useMutation, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { errorMessage, integrationsApi, isApiError, queryKeys } from '@/api';
import type { ExternalSystem, SystemStatus } from '@/api/types';
import { Badge, Button, ErrorState, Select, Spinner, TextInput, useToast } from '@/components';
import { usePageTitle } from '@/lib/usePageTitle';
import { SYSTEM_LABELS, SYSTEMS, systemLabel } from './labels';
import { pullToast, pushToast } from './summaries';
import styles from './Admin.module.css';

const SYSTEM_DESCRIPTIONS: Record<ExternalSystem, string> = {
  servicenow: 'Olay kayıtları (incident tablosu) iki yönlü senkronlanır.',
  jira_sm: 'Service desk talepleri iki yönlü senkronlanır.',
};

function SystemCard({
  status,
  pulling,
  disabled,
  onPull,
}: {
  status: SystemStatus;
  pulling: boolean;
  disabled: boolean;
  onPull: () => void;
}) {
  const name = SYSTEM_LABELS[status.system];
  const hintId = `pull-hint-${status.system}`;
  return (
    <li className={`card stack ${styles.system}`} data-testid={`system-${status.system}`}>
      <div className={styles.cardHead}>
        <h3 className={styles.systemName}>{name}</h3>
        {status.configured ? (
          <Badge tone="success" dot>
            Yapılandırıldı
          </Badge>
        ) : (
          <Badge tone="neutral" dot>
            Yapılandırılmamış
          </Badge>
        )}
      </div>
      <p className="muted" id={hintId}>
        {status.configured
          ? SYSTEM_DESCRIPTIONS[status.system]
          : 'Bağlantı bilgileri sunucu ayarlarında tanımlı değil; çekme ve gönderme yapılamaz.'}
      </p>
      <div>
        <Button
          variant="secondary"
          loading={pulling}
          disabled={!status.configured || disabled}
          aria-describedby={hintId}
          onClick={onPull}
        >
          {name} kayıtlarını çek
        </Button>
      </div>
    </li>
  );
}

function PushForm({ configured }: { configured: ExternalSystem[] }) {
  const toast = useToast();
  const [incidentId, setIncidentId] = useState('');
  const [system, setSystem] = useState<ExternalSystem | ''>('');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const push = useMutation({
    mutationFn: (v: { id: string; system: ExternalSystem | undefined }) =>
      integrationsApi.push(v.id, v.system),
    onSuccess: (events) => {
      toast.show(pushToast(events));
    },
    onError: (e) => {
      if (isApiError(e) && e.isNotFound) {
        setFieldError('Bu kimlikle bir kayıt bulunamadı.');
        return;
      }
      toast.show({ tone: 'error', title: 'Gönderim yapılamadı', description: errorMessage(e) });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const id = incidentId.trim();
    if (!id) {
      setFieldError('Kayıt kimliği zorunludur.');
      return;
    }
    setFieldError(null);
    push.mutate({ id, system: system || undefined });
  };

  const none = configured.length === 0;
  return (
    <section className="card stack" aria-labelledby="push-title">
      <div>
        <h2 id="push-title" className={styles.sectionTitle}>
          Kaydı dış sisteme gönder
        </h2>
        <p className="muted">
          Bir ServisZeka kaydının güncel hâlini bağlı sistemlere gönderir; bağlantı yoksa dış
          sistemde yeni kayıt açılır.
        </p>
      </div>
      <form className={styles.pushForm} onSubmit={submit} noValidate>
        <TextInput
          label="Kayıt kimliği"
          hint="Kayıt ayrıntı sayfasının adresindeki kimlik (ör. /kayitlar/<kimlik>)."
          required
          value={incidentId}
          error={fieldError}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setIncidentId(e.target.value);
            if (fieldError) setFieldError(null);
          }}
          className={styles.grow}
        />
        <Select
          label="Hedef sistem"
          placeholder="Yapılandırılmış tüm sistemler"
          options={SYSTEMS.map((s) => ({
            value: s,
            label: configured.includes(s)
              ? systemLabel(s)
              : `${systemLabel(s)} (yapılandırılmamış)`,
            disabled: !configured.includes(s),
          }))}
          value={system}
          onChange={(e) => {
            setSystem(e.target.value as ExternalSystem | '');
          }}
        />
        <div className={styles.formActions}>
          <Button type="submit" loading={push.isPending} disabled={none}>
            Gönder
          </Button>
        </div>
      </form>
      {none && (
        <p className="muted" role="note">
          Gönderim için önce en az bir entegrasyon yapılandırılmalı.
        </p>
      )}
    </section>
  );
}

/** /yonetim/entegrasyonlar — ServiceNow / Jira SM durumu, çekme ve kayıt gönderme */
export default function IntegrationsPage() {
  usePageTitle('Entegrasyonlar');
  const toast = useToast();
  const status = useQuery({
    queryKey: queryKeys.integrations.status,
    queryFn: ({ signal }) => integrationsApi.status(signal),
  });
  const pull = useMutation({
    mutationFn: (system: ExternalSystem) => integrationsApi.pull(system),
    onSuccess: (result, system) => {
      toast.show(pullToast(system, result));
    },
    onError: (e, system) => {
      toast.show({
        tone: 'error',
        title: `${systemLabel(system)}: çekme başarısız`,
        description: errorMessage(e),
      });
    },
  });

  const configured = (status.data ?? []).filter((s) => s.configured).map((s) => s.system);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Entegrasyonlar</h1>
          <p className="muted">
            ITSM sistemleriyle iki yönlü senkron. Senkron çakışmaları Onaylar ekranında bir insan
            tarafından çözülür.
          </p>
        </div>
      </div>

      <section className="stack" aria-labelledby="systems-title">
        <h2 id="systems-title" className={styles.sectionTitle}>
          Bağlı sistemler
        </h2>
        {status.isPending ? (
          <Spinner block label="Entegrasyon durumu yükleniyor…" />
        ) : status.isError ? (
          <ErrorState
            error={status.error}
            title="Durum alınamadı"
            onRetry={() => void status.refetch()}
          />
        ) : (
          <ul className={styles.systems}>
            {status.data.map((s) => (
              <SystemCard
                key={s.system}
                status={s}
                pulling={pull.isPending && pull.variables === s.system}
                disabled={pull.isPending}
                onPull={() => {
                  pull.mutate(s.system);
                }}
              />
            ))}
          </ul>
        )}
      </section>

      {status.isSuccess && <PushForm configured={configured} />}
    </div>
  );
}
