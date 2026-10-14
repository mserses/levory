import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type FormEvent } from 'react';
import {
  errorMessage,
  incidentsApi,
  isApiError,
  queryKeys,
  type Incident,
  type IncidentCreate,
} from '@/api';
import { useAuth } from '@/auth/useAuth';
import { Button, Select, TextArea, TextInput, optionsFrom } from '@/components';
import { URGENCY_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { RequestSuccess } from './components/RequestSuccess';
import { SafetyNotice } from './components/SafetyNotice';
import styles from './NewRequestPage.module.css';
import { SERVICE_OPTIONS } from './services';
import {
  DESCRIPTION_MAX,
  TITLE_MAX,
  validateNewRequest,
  type FormErrors,
  type NewRequestField,
  type NewRequestInput,
  type NewRequestValues,
} from './validation';

const EMPTY: NewRequestInput = { title: '', description: '', service: '', urgency: '' };

const FIELD_IDS: Record<NewRequestField, string> = {
  title: 'talep-baslik',
  description: 'talep-aciklama',
  service: 'talep-hizmet',
  urgency: 'talep-aciliyet',
};

const FIELD_LABELS: Record<NewRequestField, string> = {
  title: 'Başlık',
  description: 'Açıklama',
  service: 'Hizmet',
  urgency: 'Aciliyet',
};

const FIELD_ORDER: readonly NewRequestField[] = ['title', 'description', 'service', 'urgency'];

const URGENCY_OPTIONS = optionsFrom(URGENCY_LABELS).reverse(); // düşük → yüksek

/** 422 yanıtındaki alan hatalarını form alanlarına eşler */
function serverFieldErrors(error: unknown): FormErrors {
  if (!isApiError(error)) return {};
  const out: FormErrors = {};
  for (const fe of error.fieldErrors) {
    const key = fe.field.split('.')[0];
    if (key === 'title' || key === 'description' || key === 'service' || key === 'urgency') {
      out[key] ??= fe.message;
    }
  }
  return out;
}

/** /talep — self-servis yeni talep formu (sahibi: self-service). POST /incidents */
export default function NewRequestPage() {
  usePageTitle('Yeni talep');
  const { user, isRequesterOnly } = useAuth();
  const queryClient = useQueryClient();

  const [values, setValues] = useState<NewRequestInput>(EMPTY);
  const [errors, setErrors] = useState<FormErrors>({});
  const [showSummary, setShowSummary] = useState(false);
  const [created, setCreated] = useState<Incident | null>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const submitErrorRef = useRef<HTMLDivElement>(null);

  const create = useMutation({
    mutationFn: (v: NewRequestValues) => {
      const body: IncidentCreate = {
        title: v.title,
        description: v.description,
        kind: 'incident',
        ...(v.service ? { service: v.service } : {}),
        ...(v.urgency ? { urgency: v.urgency } : {}),
        // requester için sunucu caller'ı belirteçten yazar; diğer roller kendi adına açar
        ...(!isRequesterOnly && user ? { caller: user.username } : {}),
      };
      return incidentsApi.create(body);
    },
    onSuccess: (incident) => {
      setCreated(incident);
      setValues(EMPTY);
      setErrors({});
      setShowSummary(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.incidents.all });
    },
    onError: (error) => {
      const fieldErrs = serverFieldErrors(error);
      if (Object.keys(fieldErrs).length > 0) {
        setErrors(fieldErrs);
        setShowSummary(true);
        requestAnimationFrame(() => summaryRef.current?.focus());
      } else {
        requestAnimationFrame(() => submitErrorRef.current?.focus());
      }
    },
  });

  const update = (field: NewRequestField, value: string) => {
    setValues((prev) => ({ ...prev, [field]: value }));
    if (errors[field]) {
      setErrors((prev) => ({ ...prev, [field]: undefined }));
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (create.isPending) return;
    const result = validateNewRequest(values);
    if (!result.ok) {
      setErrors(result.errors);
      setShowSummary(true);
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    setErrors({});
    setShowSummary(false);
    create.mutate(result.values);
  };

  if (created) {
    return (
      <RequestSuccess
        incident={created}
        onNewRequest={() => {
          create.reset();
          setCreated(null);
        }}
      />
    );
  }

  const errorList = FIELD_ORDER.filter((f) => errors[f]);
  const submitError =
    create.isError && Object.keys(serverFieldErrors(create.error)).length === 0
      ? create.error
      : null;
  const descriptionLength = values.description.length;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Yeni talep</h1>
          <p className="muted">
            Yaşadığınız sorunu ya da ihtiyacınızı anlatın; ekibimiz en kısa sürede ilgilenecek.
          </p>
        </div>
      </div>

      <div className={styles.layout}>
        <section className="card" aria-labelledby="talep-form-baslik">
          <h2 id="talep-form-baslik" className="visually-hidden">
            Talep formu
          </h2>
          <form className={styles.form} onSubmit={onSubmit} noValidate aria-busy={create.isPending}>
            {showSummary && errorList.length > 0 && (
              <div
                ref={summaryRef}
                className={styles.summary}
                role="alert"
                tabIndex={-1}
                aria-labelledby="talep-hata-ozeti"
              >
                <h2 id="talep-hata-ozeti">Lütfen şu alanları düzeltin:</h2>
                <ul>
                  {errorList.map((f) => (
                    <li key={f}>
                      <a href={`#${FIELD_IDS[f]}`}>
                        {FIELD_LABELS[f]}: {errors[f]}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <SafetyNotice />

            <TextInput
              id={FIELD_IDS.title}
              name="title"
              label="Başlık"
              hint="Tek cümleyle özetleyin. Örn. “VPN’e evden bağlanamıyorum”."
              required
              maxLength={TITLE_MAX}
              autoComplete="off"
              value={values.title}
              error={errors.title}
              onChange={(e) => {
                update('title', e.target.value);
              }}
            />

            <TextArea
              id={FIELD_IDS.description}
              name="description"
              label="Açıklama"
              hint="Ne oldu, ne zamandan beri, hangi hata mesajını görüyorsunuz, kimler etkileniyor?"
              required
              rows={8}
              value={values.description}
              error={errors.description}
              onChange={(e) => {
                update('description', e.target.value);
              }}
            />
            <p
              className={
                descriptionLength > DESCRIPTION_MAX
                  ? `${styles.counter ?? ''} ${styles.counterOver ?? ''}`
                  : styles.counter
              }
              aria-hidden="true"
            >
              {descriptionLength} / {DESCRIPTION_MAX}
            </p>

            <div className={styles.twoCol}>
              <Select
                id={FIELD_IDS.service}
                name="service"
                label="Hizmet (isteğe bağlı)"
                hint="Emin değilseniz boş bırakın; doğru ekip otomatik belirlenir."
                placeholder="Bilmiyorum / listede yok"
                options={SERVICE_OPTIONS}
                value={values.service}
                error={errors.service}
                onChange={(e) => {
                  update('service', e.target.value);
                }}
              />
              <Select
                id={FIELD_IDS.urgency}
                name="urgency"
                label="Aciliyet (isteğe bağlı)"
                hint="Sizin için ne kadar acil? Son öncelik destek ekibince belirlenir."
                placeholder="Belirtmek istemiyorum"
                options={URGENCY_OPTIONS}
                value={values.urgency}
                error={errors.urgency}
                onChange={(e) => {
                  update('urgency', e.target.value);
                }}
              />
            </div>

            {submitError !== null && (
              <div ref={submitErrorRef} className={styles.summary} role="alert" tabIndex={-1}>
                <h2>Talebiniz gönderilemedi</h2>
                <p>{errorMessage(submitError)}</p>
                <p>Yazdıklarınız kaybolmadı; birazdan yeniden deneyebilirsiniz.</p>
              </div>
            )}

            <div className={styles.actions}>
              <Button type="submit" loading={create.isPending}>
                {create.isPending ? 'Gönderiliyor…' : 'Talebi gönder'}
              </Button>
              <Button
                variant="ghost"
                disabled={create.isPending}
                onClick={() => {
                  setValues(EMPTY);
                  setErrors({});
                  setShowSummary(false);
                }}
              >
                Formu temizle
              </Button>
            </div>
          </form>
        </section>

        <aside className={`card ${styles.aside ?? ''}`} aria-labelledby="talep-surec-baslik">
          <div>
            <h2 id="talep-surec-baslik">Gönderdikten sonra ne olur?</h2>
            <ol>
              <li>Talebinize bir kayıt numarası verilir.</li>
              <li>Yapay zekâ asistanı talebi sınıflandırır ve doğru ekibi önerir.</li>
              <li>Bir destek uzmanı öneriyi inceler, onaylar ve talebinizle ilgilenir.</li>
            </ol>
          </div>
          <div>
            <h2>Kişisel verileriniz (KVKK)</h2>
            <p className="muted">
              Bu formda paylaştığınız bilgiler 6698 sayılı Kişisel Verilerin Korunması Kanunu
              kapsamında yalnızca talebinizin karşılanması amacıyla işlenir. Talebiniz, yapay zekâ
              destekli sınıflandırmadan geçer ve yetkili destek personelince görüntülenir.
              Gerekmedikçe TC kimlik numarası, sağlık bilgisi gibi özel nitelikli veriler veya
              başkalarına ait kişisel bilgiler paylaşmayın.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
