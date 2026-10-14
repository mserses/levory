import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { errorMessage, improvementsApi, isApiError, queryKeys } from '@/api';
import type { Improvement } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import {
  Badge,
  Button,
  Dialog,
  ErrorState,
  Spinner,
  TextArea,
  buttonClassName,
  useToast,
} from '@/components';
import { useKpiDefinitions } from '@/features/kpi/useKpiDefinitions';
import { LOCALE, formatDateTime } from '@/lib/format';
import { IMPROVEMENT_SOURCE_LABELS, IMPROVEMENT_STATE_LABELS, actorLabel } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { ImprovementStateBadge } from './ImprovementStateBadge';
import { kpiName, scopeText } from './KpiScope';
import { TargetBadge } from './TargetBadge';
import {
  STEP_LABELS,
  availableSteps,
  evidenceValue,
  measureDueAt,
  parseMeasurement,
  stepNote,
  timeline,
  unitFor,
  valueText,
  type ImprovementStep,
  type KpiUnit,
} from './model';
import styles from './Improvements.module.css';

/** /iyilestirmeler/:id — CSI ayrıntısı (sahibi: improvements). GET /improvements/{id} */
export default function ImprovementDetailPage() {
  const { id = '' } = useParams();
  const query = useQuery({
    queryKey: queryKeys.improvements.detail(id),
    queryFn: ({ signal }) => improvementsApi.get(id, signal),
  });
  usePageTitle(query.data ? `${query.data.number} · ${query.data.title}` : 'İyileştirme');

  if (query.isPending) {
    return (
      <div className="page">
        <Spinner block label="İyileştirme yükleniyor…" showLabel />
      </div>
    );
  }
  if (query.isError) {
    const notFound = isApiError(query.error) && query.error.isNotFound;
    return (
      <div className="page">
        <h1>İyileştirme</h1>
        <ErrorState
          error={query.error}
          title={notFound ? 'İyileştirme bulunamadı' : 'İyileştirme yüklenemedi'}
          {...(notFound ? {} : { onRetry: () => void query.refetch() })}
          action={
            <Link to="/iyilestirmeler" className={buttonClassName('secondary')}>
              İyileştirmelere dön
            </Link>
          }
        />
      </div>
    );
  }
  return <ImprovementDetail improvement={query.data} />;
}

const pct = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 });

function deltaLabel(deltaPct: number | null, improved: boolean | null): string | null {
  if (deltaPct === null) return null;
  const arrow = deltaPct < 0 ? '▼' : deltaPct > 0 ? '▲' : '■';
  const verdict = improved === null ? '' : improved ? ' (iyileşti)' : ' (iyileşmedi)';
  return `${arrow} %${pct.format(Math.abs(deltaPct))}${verdict}`;
}

function ImprovementDetail({ improvement: imp }: { improvement: Improvement }) {
  const { user } = useAuth();
  const defs = useKpiDefinitions().data;
  const [step, setStep] = useState<ImprovementStep | null>(null);
  const steps = availableSteps(imp, user?.roles ?? []);
  const unit = unitFor(imp, defs);
  const scope = scopeText(imp);

  return (
    <div className="page">
      <header className="page-header">
        <div className="stack" style={{ gap: 'var(--space-2)' }}>
          <p className="muted" style={{ margin: 0 }}>
            <Link to="/iyilestirmeler">İyileştirmeler</Link> · {imp.number}
          </p>
          <h1>
            {imp.number} · {imp.title}
          </h1>
          <div className={styles.badges}>
            <ImprovementStateBadge state={imp.state} />
            <Badge>{IMPROVEMENT_SOURCE_LABELS[imp.source]}</Badge>
            {imp.detected_by.startsWith('ai:') && <Badge tone="accent">Yapay zekâ önerisi</Badge>}
          </div>
        </div>
        {steps.length > 0 && (
          <div className={styles.actions} role="group" aria-label="Durum adımları">
            {steps.map((s) => (
              <Button
                key={s}
                variant={s === 'reject' ? 'secondary' : 'primary'}
                onClick={() => {
                  setStep(s);
                }}
              >
                {STEP_LABELS[s]}
              </Button>
            ))}
          </div>
        )}
      </header>

      <div className={styles.layout}>
        <div className="stack">
          <MeasurementCard improvement={imp} unit={unit} name={kpiName(imp, defs)} scope={scope} />

          <section className="card" aria-labelledby="csi-aciklama">
            <h2 id="csi-aciklama" className={styles.sectionHeading}>
              Açıklama
            </h2>
            <p className="prewrap" style={{ margin: 0 }}>
              {imp.description || '—'}
            </p>
          </section>

          <section className="card" aria-labelledby="csi-kanit">
            <h2 id="csi-kanit" className={styles.sectionHeading}>
              Kanıt
            </h2>
            {(imp.problem_id ?? imp.change_id) && (
              <ul className={styles.list} style={{ marginBottom: 'var(--space-3)' }}>
                {imp.problem_id && (
                  <li>
                    <Link to={`/problemler/${encodeURIComponent(imp.problem_id)}`}>
                      Bağlı sorun kaydı
                    </Link>
                  </li>
                )}
                {imp.change_id && (
                  <li>
                    <Link to={`/degisiklikler/${encodeURIComponent(imp.change_id)}`}>
                      Bağlı değişiklik
                    </Link>
                  </li>
                )}
              </ul>
            )}
            {imp.evidence && Object.keys(imp.evidence).length > 0 ? (
              <dl className={styles.facts}>
                {Object.entries(imp.evidence).map(([key, value]) => (
                  <div key={key} style={{ display: 'contents' }}>
                    <dt className={styles.evidenceKey}>{key}</dt>
                    <dd className={styles.evidenceValue}>{evidenceValue(value)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                Ek kanıt verisi yok.
              </p>
            )}
          </section>
        </div>

        <div className="stack">
          <section className="card" aria-labelledby="csi-ozet">
            <h2 id="csi-ozet" className={styles.sectionHeading}>
              Özet
            </h2>
            <dl className={styles.facts}>
              <dt>Sahip</dt>
              <dd>{imp.owner ? actorLabel(imp.owner) : '—'}</dd>
              <dt>Öneren</dt>
              <dd>{actorLabel(imp.detected_by)}</dd>
              <dt>Fayda / efor</dt>
              <dd>
                {imp.benefit_estimate} / {imp.effort} (puan {Math.round(imp.priority_score)})
              </dd>
              <dt>Ölçüm süresi</dt>
              <dd>{imp.measure_days} gün</dd>
              <dt>Güncelleme</dt>
              <dd>{formatDateTime(imp.updated_at)}</dd>
            </dl>
          </section>
          <section className="card" aria-labelledby="csi-gecmis">
            <h2 id="csi-gecmis" className={styles.sectionHeading}>
              Zaman çizelgesi
            </h2>
            <ol className={styles.list}>
              {timeline(imp).map((e) => (
                <li key={`${e.label}-${e.at}`}>
                  <strong>{e.label}</strong>
                  <span className={styles.meta}>
                    {e.actor ? `${actorLabel(e.actor)} · ` : ''}
                    {formatDateTime(e.at)}
                  </span>
                  {e.note && <span className="prewrap">{e.note}</span>}
                </li>
              ))}
            </ol>
          </section>
        </div>
      </div>

      {step && (
        <StepDialog
          improvement={imp}
          step={step}
          onClose={() => {
            setStep(null);
          }}
        />
      )}
    </div>
  );
}

function MeasurementCard({
  improvement: imp,
  unit,
  name,
  scope,
}: {
  improvement: Improvement;
  unit: KpiUnit;
  name: string;
  scope: string | null;
}) {
  const m = parseMeasurement(imp);
  const due = measureDueAt(imp);
  const delta = m ? deltaLabel(m.deltaPct, m.improved) : null;
  return (
    <section className="card" aria-labelledby="csi-olcum">
      <h2 id="csi-olcum" className={styles.sectionHeading}>
        Ölçüm
      </h2>
      <p style={{ margin: '0 0 var(--space-3)' }}>
        <strong>{name}</strong>
        {scope && <span className="muted"> · {scope}</span>}
      </p>
      <dl className={styles.measures}>
        <div>
          <dt>Taban</dt>
          <dd>{valueText(imp.baseline_value, unit)}</dd>
        </div>
        <div>
          <dt>Hedef</dt>
          <dd>{valueText(imp.target_value, unit)}</dd>
        </div>
        <div>
          <dt>Ölçülen</dt>
          <dd>
            {imp.measured_value === null ? (
              <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                Henüz ölçülmedi
              </span>
            ) : (
              valueText(imp.measured_value, unit)
            )}
          </dd>
        </div>
      </dl>
      <div className={styles.badges} style={{ marginTop: 'var(--space-3)' }}>
        <TargetBadge met={imp.target_met} />
        {delta && <Badge tone={m?.improved ? 'success' : 'neutral'}>{delta}</Badge>}
      </div>
      <ul className={styles.list} style={{ marginTop: 'var(--space-3)' }}>
        {imp.baseline_start && imp.baseline_end && (
          <li className={styles.meta}>
            Taban penceresi: {formatDateTime(imp.baseline_start)} –{' '}
            {formatDateTime(imp.baseline_end)}
          </li>
        )}
        {due && (
          <li className={styles.meta}>
            Ölçüm penceresi: {formatDateTime(imp.implemented_at)} – {formatDateTime(due)}
          </li>
        )}
        {m?.after?.window && (
          <li className={styles.meta}>
            Sonra: {formatDateTime(m.after.window.start)} – {formatDateTime(m.after.window.end)}
            {m.after.count !== undefined && ` · ${String(m.after.count)} kayıt`}
          </li>
        )}
      </ul>
    </section>
  );
}

function StepDialog({
  improvement,
  step,
  onClose,
}: {
  improvement: Improvement;
  step: ImprovementStep;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const need = stepNote(step, improvement);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () => {
      const { id, version } = improvement;
      const text = note.trim() || undefined;
      switch (step) {
        case 'approve':
          return improvementsApi.approve(id, version, text);
        case 'reject':
          return improvementsApi.reject(id, version, note.trim());
        case 'start':
          return improvementsApi.start(id, version, text);
        case 'implemented':
          return improvementsApi.implemented(id, version);
        case 'measure':
          return improvementsApi.measure(id, version);
        case 'close':
          return improvementsApi.close(id, version, text);
      }
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKeys.improvements.detail(improvement.id), updated);
      void queryClient.invalidateQueries({ queryKey: queryKeys.improvements.lists });
      toast.show({ title: `Durum: ${IMPROVEMENT_STATE_LABELS[updated.state]}`, tone: 'success' });
      onClose();
    },
    onError: (e) => {
      if (isApiError(e) && e.isConflict) {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.improvements.detail(improvement.id),
        });
      }
      setFormError(errorMessage(e));
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (need === 'required' && !note.trim()) {
      setError('Gerekçe zorunludur.');
      return;
    }
    setError(null);
    setFormError(null);
    mutation.mutate();
  };
  const description =
    step === 'measure'
      ? 'Uygulama sonrası pencerede KPI yeniden hesaplanır ve tabanla karşılaştırılır. Pencere dolmadıysa ölçülmez.'
      : step === 'implemented'
        ? 'Uygulamanın bittiği an şimdi olarak işaretlenir; ölçüm penceresi buradan başlar.'
        : undefined;
  return (
    <Dialog
      open
      onClose={onClose}
      dismissible={!mutation.isPending}
      title={STEP_LABELS[step]}
      description={description}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.isPending}>
            Vazgeç
          </Button>
          <Button type="submit" form="csi-step" loading={mutation.isPending}>
            Kaydet
          </Button>
        </>
      }
    >
      <form id="csi-step" className={styles.form} onSubmit={submit} noValidate>
        {need !== 'none' && (
          <TextArea
            label={need === 'required' ? 'Gerekçe' : 'Not (isteğe bağlı)'}
            required={need === 'required'}
            maxLength={10_000}
            rows={3}
            value={note}
            error={error}
            onChange={(e) => {
              setNote(e.target.value);
            }}
          />
        )}
        {formError && (
          <p role="alert" className={styles.formError}>
            {formError}
          </p>
        )}
      </form>
    </Dialog>
  );
}
