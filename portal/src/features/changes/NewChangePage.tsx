import { useId, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { changesApi, errorMessage } from '@/api';
import type { Change, ConfigurationItem, ScheduleConflict } from '@/api/types';
import { Button, Dialog, Select, Spinner, TextArea, TextInput, useToast } from '@/components';
import { CHANGE_TYPE_DESCRIPTIONS, CHANGE_TYPE_LABELS, RISK_LEVEL_LABELS } from '@/lib/labels';
import { taxonomyOptions } from '@/lib/taxonomy';
import { usePageTitle } from '@/lib/usePageTitle';
import { windowConflicts } from './calendar';
import styles from './Changes.module.css';
import { CIPicker } from './components/CIPicker';
import { ConflictList } from './components/ConflictList';
import { ImpactSummary } from './components/ImpactSummary';
import {
  EMPTY_CHANGE_FORM,
  TITLE_MAX,
  parseLocalDateTime,
  toChangeCreate,
  validateChangeForm,
  type ChangeFormErrors,
  type ChangeFormValues,
} from './form';
import { useCalendar, useCreateChange, useImpactPreview, useTemplates } from './hooks';

const TYPE_ORDER = ['normal', 'emergency', 'standard'] as const;

/** /degisiklikler/yeni — değişiklik talebi (RFC) formu (sahibi: changes). POST /changes */
export default function NewChangePage() {
  usePageTitle('Yeni değişiklik');
  const navigate = useNavigate();
  const toast = useToast();
  const [values, setValues] = useState<ChangeFormValues>(EMPTY_CHANGE_FORM);
  const [cis, setCis] = useState<ConfigurationItem[]>([]);
  const [errors, setErrors] = useState<ChangeFormErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [conflictResult, setConflictResult] = useState<{
    change: Change;
    conflicts: ScheduleConflict[];
  } | null>(null);
  const create = useCreateChange();
  const templates = useTemplates(values.changeType === 'standard');
  const ciIds = useMemo(() => cis.map((c) => c.id), [cis]);
  const impact = useImpactPreview(ciIds);
  const typeHelpId = useId();

  const set = <K extends keyof ChangeFormValues>(key: K, value: ChangeFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
  };

  const applyTemplate = (id: string) => {
    const t = templates.data?.find((x) => x.id === id);
    setValues((v) => ({
      ...v,
      templateId: id,
      ...(t
        ? {
            implementationPlan: v.implementationPlan || t.implementation_plan,
            backoutPlan: v.backoutPlan || t.backout_plan,
            testPlan: v.testPlan || t.test_plan,
            title: v.title || t.name,
          }
        : {}),
    }));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setServerError(null);
    const found = validateChangeForm({ ...values, ciIds });
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    let created: Change;
    try {
      created = await create.mutateAsync(toChangeCreate({ ...values, ciIds }));
    } catch (error) {
      setServerError(errorMessage(error));
      return;
    }
    // Sunucunun kesin çakışma denetimi (dondurma + aynı CI)
    let conflicts: ScheduleConflict[] = [];
    if (created.planned_start && created.planned_end) {
      try {
        conflicts = await changesApi.conflicts(created.id);
      } catch {
        conflicts = [];
      }
    }
    if (conflicts.length > 0) {
      setConflictResult({ change: created, conflicts });
      return;
    }
    toast.show({ title: `${created.number} taslak olarak kaydedildi`, tone: 'success' });
    navigate(`/degisiklikler/${encodeURIComponent(created.id)}`);
  };

  const selectedTemplate = templates.data?.find((t) => t.id === values.templateId);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <Link to="/degisiklikler">← Değişikliklere dön</Link>
          <h1>Yeni değişiklik talebi</h1>
          <p className="muted" style={{ margin: 0 }}>
            Talep taslak olarak kaydedilir. Değerlendirmeye göndermek için uygulama planı, geri
            dönüş planı ve planlanan pencere gerekir.
          </p>
        </div>
      </header>
      <div className={styles.layout}>
        <form className={`card ${styles.form}`} noValidate onSubmit={(e) => void submit(e)}>
          <fieldset className={styles.fieldset} aria-describedby={typeHelpId}>
            <legend>Değişiklik türü</legend>
            {TYPE_ORDER.map((type) => (
              <label key={type} className={styles.radioCard}>
                <input
                  type="radio"
                  name="change_type"
                  value={type}
                  checked={values.changeType === type}
                  onChange={() => {
                    set('changeType', type);
                  }}
                />
                <span>
                  {CHANGE_TYPE_LABELS[type]}
                  {type === 'standard' ? ' (şablondan)' : ''}
                </span>
                <p aria-hidden="true">{CHANGE_TYPE_DESCRIPTIONS[type]}</p>
              </label>
            ))}
            <p id={typeHelpId} className="visually-hidden">
              {TYPE_ORDER.map(
                (t) => `${CHANGE_TYPE_LABELS[t]}: ${CHANGE_TYPE_DESCRIPTIONS[t]}`,
              ).join(' ')}
            </p>
          </fieldset>

          {values.changeType === 'standard' &&
            (templates.isPending ? (
              <Spinner label="Şablonlar yükleniyor…" />
            ) : (
              <Select
                label="Standart şablon"
                required
                placeholder="Şablon seçin"
                options={(templates.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
                value={values.templateId}
                error={errors.templateId}
                hint={
                  selectedTemplate
                    ? `${selectedTemplate.description || 'Açıklama yok'} · En fazla risk: ${RISK_LEVEL_LABELS[selectedTemplate.max_risk]}`
                    : 'Önceden onaylı katalog öğesi; boş planlar şablondan doldurulur.'
                }
                onChange={(e) => {
                  applyTemplate(e.target.value);
                }}
              />
            ))}

          <TextInput
            label="Başlık"
            required
            maxLength={TITLE_MAX}
            value={values.title}
            error={errors.title}
            onChange={(e) => {
              set('title', e.target.value);
            }}
          />
          <TextArea
            label="Açıklama"
            value={values.description}
            onChange={(e) => {
              set('description', e.target.value);
            }}
          />

          <section className="stack" aria-label="Etkilenen yapılandırma öğeleri">
            <h2 className={styles.sectionTitle}>Etkilenen CI’lar</h2>
            <CIPicker selected={cis} onChange={setCis} />
          </section>

          <section className="stack" aria-label="Planlar">
            <h2 className={styles.sectionTitle}>Planlar</h2>
            <TextArea
              label="Uygulama planı"
              hint="Adım adım ne yapılacak? Değerlendirmeye göndermek için zorunlu."
              value={values.implementationPlan}
              onChange={(e) => {
                set('implementationPlan', e.target.value);
              }}
            />
            <TextArea
              label="Geri dönüş planı"
              hint="Başarısızlıkta nasıl geri dönülecek? Yoksa risk skoru artar."
              value={values.backoutPlan}
              onChange={(e) => {
                set('backoutPlan', e.target.value);
              }}
            />
            <TextArea
              label="Test planı"
              value={values.testPlan}
              onChange={(e) => {
                set('testPlan', e.target.value);
              }}
            />
          </section>

          <section className="stack" aria-label="Planlanan pencere">
            <h2 className={styles.sectionTitle}>Planlanan pencere</h2>
            <div className={styles.twoCol}>
              <TextInput
                label="Planlanan başlangıç"
                type="datetime-local"
                value={values.plannedStart}
                error={errors.plannedStart}
                onChange={(e) => {
                  set('plannedStart', e.target.value);
                }}
              />
              <TextInput
                label="Planlanan bitiş"
                type="datetime-local"
                value={values.plannedEnd}
                error={errors.plannedEnd}
                onChange={(e) => {
                  set('plannedEnd', e.target.value);
                }}
              />
            </div>
            <WindowCheck values={values} cis={cis} />
          </section>

          <div className={styles.twoCol}>
            <Select
              label="Atama grubu"
              placeholder="Seçilmedi"
              options={taxonomyOptions('assignment_group')}
              value={values.assignmentGroup}
              onChange={(e) => {
                set('assignmentGroup', e.target.value);
              }}
            />
            <TextInput
              label="Uygulayıcı (kullanıcı adı)"
              hint="Talep eden ve uygulayıcı bu değişiklik için CAB oyu veremez."
              value={values.assignee}
              error={errors.assignee}
              onChange={(e) => {
                set('assignee', e.target.value);
              }}
            />
          </div>

          {serverError && (
            <p role="alert" className={styles.errorText}>
              Kaydedilemedi: {serverError}
            </p>
          )}
          <div className={styles.actions}>
            <Button type="submit" loading={create.isPending}>
              Taslağı kaydet
            </Button>
            <Link to="/degisiklikler">Vazgeç</Link>
          </div>
        </form>

        <aside className="stack">
          {cis.length > 0 ? (
            <section className="card stack" aria-label="Etki önizlemesi">
              <h2 className={styles.sectionTitle}>Etki önizlemesi</h2>
              <p className="muted" style={{ margin: 0 }}>
                Seçilen CI’lar değişirse etkilenebilecek öğeler (CMDB etki analizi).
              </p>
              {impact.isError ? (
                <p role="alert" className={styles.errorText}>
                  Etki analizi alınamadı: {errorMessage(impact.error)}
                </p>
              ) : impact.data ? (
                <ImpactSummary impact={impact.data} />
              ) : (
                <Spinner label="Etki hesaplanıyor…" />
              )}
            </section>
          ) : (
            <section className="card" aria-label="Etki önizlemesi bilgisi">
              <p className="muted" style={{ margin: 0 }}>
                Etkilenen CI seçtiğinizde, bu değişiklikten etkilenebilecek öğeler burada
                listelenir.
              </p>
            </section>
          )}
        </aside>
      </div>

      <Dialog
        open={conflictResult !== null}
        role="alertdialog"
        dismissible={false}
        onClose={() => undefined}
        title="Planlanan pencerede çakışma var"
        description={
          conflictResult
            ? `${conflictResult.change.number} taslak olarak kaydedildi, ancak planlanan pencere çakışıyor. Zamanlamadan önce pencereyi değiştirin ya da çakışmayı giderin.`
            : undefined
        }
        footer={
          <Button
            data-autofocus
            onClick={() => {
              if (conflictResult)
                navigate(`/degisiklikler/${encodeURIComponent(conflictResult.change.id)}`);
            }}
          >
            Taslağa git
          </Button>
        }
      >
        <ConflictList conflicts={conflictResult?.conflicts ?? []} />
      </Dialog>
    </div>
  );
}

/** Kaydetmeden önce takvime göre olası çakışmaları gösterir (sunucu ayrıca denetler) */
function WindowCheck({ values, cis }: { values: ChangeFormValues; cis: ConfigurationItem[] }) {
  const start = parseLocalDateTime(values.plannedStart);
  const end = parseLocalDateTime(values.plannedEnd);
  const valid = start !== null && end !== null && end > start;
  const calendar = useCalendar(start ?? new Date(0), end ?? new Date(0), valid);
  if (!valid) return null;
  if (calendar.isPending) return <Spinner label="Takvim denetleniyor…" />;
  if (calendar.isError)
    return (
      <p className="muted" style={{ margin: 0 }}>
        Takvim denetlenemedi; çakışmalar kaydettikten sonra sunucuda denetlenir.
      </p>
    );
  const reasons = windowConflicts(
    {
      start,
      end,
      ciIds: cis.map((c) => c.id),
      changeType: values.changeType,
      environments: cis.map((c) => c.environment),
    },
    calendar.data.changes,
    calendar.data.freeze_windows,
  );
  if (reasons.length === 0)
    return (
      <p className="muted" style={{ margin: 0 }}>
        Takvimde bu pencereyle çakışan dondurma ya da değişiklik görünmüyor.
      </p>
    );
  return (
    <div role="status" aria-label="Olası çakışmalar" className={styles.warningBox}>
      <strong>Olası çakışmalar</strong>
      <ul>
        {reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      <span>Bu pencereyle zamanlama sunucuda reddedilebilir.</span>
    </div>
  );
}
