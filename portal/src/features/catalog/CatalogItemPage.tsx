import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { catalogApi, isApiError, queryKeys, requestsApi, type CatalogItem } from '@/api';
import { useAuth } from '@/auth/useAuth';
import {
  Badge,
  Button,
  ErrorState,
  Field,
  Select,
  Spinner,
  TextArea,
  TextInput,
  buttonClassName,
  useToast,
} from '@/components';
import { CATALOG_APPROVAL_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import {
  enumOptionLabel,
  initialValues,
  parseFormSchema,
  toPayload,
  validateForm,
  type FormErrors,
  type FormField,
  type FormValues,
} from './formSchema';
import styles from './Catalog.module.css';

/** /katalog/:key — katalog öğesi formu (JSON şeması alt kümesinden) → POST /requests → talep ayrıntısı */
export default function CatalogItemPage() {
  const { key = '' } = useParams();
  const query = useQuery({
    queryKey: queryKeys.catalog.detail(key),
    queryFn: ({ signal }) => catalogApi.get(key, signal),
  });
  usePageTitle(query.data?.name ?? 'Hizmet kataloğu');

  if (query.isPending) {
    return (
      <div className="page">
        <Spinner block label="Hizmet yükleniyor…" showLabel />
      </div>
    );
  }
  if (query.isError) {
    const notFound = isApiError(query.error) && query.error.isNotFound;
    return (
      <div className="page">
        <h1>Hizmet kataloğu</h1>
        <ErrorState
          error={query.error}
          title={notFound ? 'Hizmet bulunamadı' : 'Hizmet yüklenemedi'}
          {...(notFound ? {} : { onRetry: () => void query.refetch() })}
          action={
            <Link to="/katalog" className={buttonClassName('secondary')}>
              Kataloğa dön
            </Link>
          }
        />
      </div>
    );
  }
  return <ItemForm key={query.data.id} item={query.data} />;
}

function ItemForm({ item }: { item: CatalogItem }) {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const canRequestForOthers = hasRole('agent');
  const { fields } = useMemo(() => parseFormSchema(item.form_schema), [item.form_schema]);
  const [values, setValues] = useState<FormValues>(() => initialValues(fields));
  const [onBehalf, setOnBehalf] = useState('');
  const [errors, setErrors] = useState<FormErrors>({});
  const [serverErrors, setServerErrors] = useState<string[]>([]);
  const summaryRef = useRef<HTMLDivElement>(null);
  const [focusSummary, setFocusSummary] = useState(0);

  useEffect(() => {
    if (focusSummary > 0) summaryRef.current?.focus();
  }, [focusSummary]);

  const submit = useMutation({
    mutationFn: () =>
      requestsApi.create({
        item: item.key,
        form_values: toPayload(fields, values),
        ...(canRequestForOthers && onBehalf.trim() ? { requester: onBehalf.trim() } : {}),
      }),
    onSuccess: (created) => {
      queryClient.setQueryData(queryKeys.requests.detail(created.id), created);
      void queryClient.invalidateQueries({ queryKey: queryKeys.requests.lists });
      toast.show({ title: `${created.number} oluşturuldu`, tone: 'success' });
      navigate(`/taleplerim/${encodeURIComponent(created.id)}`);
    },
    onError: (e) => {
      const messages =
        isApiError(e) && e.fieldErrors.length > 0
          ? e.fieldErrors.map((f) => f.message)
          : [isApiError(e) ? e.detail : 'Talep gönderilemedi.'];
      setServerErrors(messages);
      setFocusSummary((n) => n + 1);
    },
  });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found = validateForm(fields, values);
    setErrors(found);
    setServerErrors([]);
    if (Object.keys(found).length > 0) {
      setFocusSummary((n) => n + 1);
      return;
    }
    submit.mutate();
  };

  const setValue = (name: string, value: string | boolean) => {
    setValues((v) => ({ ...v, [name]: value }));
    if (errors[name]) {
      setErrors((e) => Object.fromEntries(Object.entries(e).filter(([k]) => k !== name)));
    }
  };

  const fieldErrorList = fields.filter((f) => errors[f.name]);
  const showSummary = fieldErrorList.length > 0 || serverErrors.length > 0;
  const fieldId = (name: string) => `katalog-alan-${name}`;
  const plan = item.fulfilment_plan;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="muted" style={{ margin: 0 }}>
            <Link to="/katalog">Hizmet kataloğu</Link> · {item.category}
          </p>
          <h1>{item.name}</h1>
          <p className="muted prewrap" style={{ margin: 0 }}>
            {item.description}
          </p>
        </div>
      </header>

      <div className={styles.layout}>
        <form className={`card ${styles.form}`} noValidate onSubmit={onSubmit}>
          <h2 style={{ margin: 0 }}>Talep formu</h2>
          {showSummary && (
            <div ref={summaryRef} tabIndex={-1} role="alert" className={styles.summary}>
              <h2>Talep gönderilemedi</h2>
              <ul>
                {fieldErrorList.map((f) => (
                  <li key={f.name}>
                    <a href={`#${fieldId(f.name)}`}>{errors[f.name]}</a>
                  </li>
                ))}
                {serverErrors.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </div>
          )}
          {fields.length === 0 && (
            <p className="muted" style={{ margin: 0 }}>
              Bu hizmet için ek bilgi gerekmiyor; göndermeniz yeterli.
            </p>
          )}
          {canRequestForOthers && (
            <TextInput
              label="Başkası adına (isteğe bağlı)"
              hint="Boş bırakırsanız talep sizin adınıza açılır. Kullanıcı adı ya da e-posta (UPN)."
              autoComplete="off"
              maxLength={200}
              value={onBehalf}
              onChange={(e) => {
                setOnBehalf(e.target.value);
              }}
            />
          )}
          {fields.map((f) => (
            <SchemaField
              key={f.name}
              field={f}
              id={fieldId(f.name)}
              value={values[f.name]}
              error={errors[f.name]}
              username={user?.username ?? ''}
              onChange={(v) => {
                setValue(f.name, v);
              }}
            />
          ))}
          <div className={styles.formActions}>
            <Button type="submit" loading={submit.isPending}>
              Talebi gönder
            </Button>
            <Link to="/katalog" className={buttonClassName('ghost')}>
              Vazgeç
            </Link>
          </div>
        </form>

        <aside className="card stack" aria-labelledby="katalog-neler-olacak">
          <h2 id="katalog-neler-olacak" className={styles.sideHeading}>
            Neler olacak?
          </h2>
          <div className={styles.badges}>
            {item.standard && (
              <Badge tone="success" dot>
                Standart talep
              </Badge>
            )}
            <Badge tone={item.approval_policy === 'none' ? 'neutral' : 'warning'}>
              {CATALOG_APPROVAL_LABELS[item.approval_policy]}
            </Badge>
          </div>
          {item.approval_policy !== 'none' && (
            <p className="muted" style={{ margin: 0 }}>
              Talebiniz önce{' '}
              {item.approval_policy === 'manager' ? 'yöneticinizin' : 'onay grubunun'} onayına
              gider; onaylanınca karşılama başlar.
            </p>
          )}
          {plan.length > 0 && (
            <ol className={styles.plan}>
              {plan.map((step, i) => (
                <li key={i}>
                  {typeof step.title === 'string' ? step.title : `Adım ${String(i + 1)}`}
                </li>
              ))}
            </ol>
          )}
          <p className="muted" style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
            Riskli adımlar (ör. yetkili grup erişimi) her zaman BT onayı ister; ilerlemeyi
            Taleplerim sayfasından izleyebilirsiniz.
          </p>
        </aside>
      </div>
    </div>
  );
}

interface SchemaFieldProps {
  field: FormField;
  id: string;
  value: string | boolean | undefined;
  error: string | undefined;
  username: string;
  onChange: (value: string | boolean) => void;
}

function SchemaField({ field, id, value, error, username, onChange }: SchemaFieldProps) {
  const text = typeof value === 'string' ? value : '';
  const common = { id, label: field.title, required: field.required, error };

  if (field.kind === 'boolean') {
    const hintId = field.description ? `${id}-hint` : undefined;
    const errorId = error ? `${id}-error` : undefined;
    const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;
    return (
      <div className={styles.check}>
        <input
          id={id}
          type="checkbox"
          checked={value === true}
          required={field.required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => {
            onChange(e.target.checked);
          }}
        />
        <label htmlFor={id} className={styles.checkLabel}>
          {field.title}
          {field.required && <span aria-hidden="true"> *</span>}
        </label>
        {field.description && (
          <p id={hintId} className={styles.checkHint}>
            {field.description}
          </p>
        )}
        {error && (
          <p id={errorId} className={styles.checkError}>
            {error}
          </p>
        )}
      </div>
    );
  }

  if (field.kind === 'enum') {
    return (
      <Select
        {...common}
        hint={field.description}
        placeholder={field.required && field.defaultValue !== undefined ? undefined : 'Seçiniz'}
        options={(field.options ?? []).map((o) => ({ value: o, label: enumOptionLabel(o) }))}
        value={text}
        onChange={(e) => {
          onChange(e.target.value);
        }}
      />
    );
  }

  if (field.kind === 'user' || field.kind === 'group') {
    const isUser = field.kind === 'user';
    const hint =
      field.description ??
      (isUser
        ? 'Kullanıcı adı ya da e-posta (UPN), ör. ayse.yilmaz@kurum.com.tr'
        : 'Dizindeki grup adı, ör. sg-yazilim-standart');
    return (
      <div className={styles.pickerRow}>
        <Field {...common} hint={hint}>
          {(p) => (
            <input
              {...p}
              type="text"
              inputMode={isUser ? 'email' : 'text'}
              autoComplete="off"
              spellCheck={false}
              maxLength={field.maxLength}
              data-picker={field.kind}
              value={text}
              onChange={(e) => {
                onChange(e.target.value);
              }}
            />
          )}
        </Field>
        {isUser && username && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            aria-label={`${field.title}: kendimi seç`}
            onClick={() => {
              onChange(username);
            }}
          >
            Kendim
          </Button>
        )}
      </div>
    );
  }

  if (field.multiline) {
    return (
      <TextArea
        {...common}
        hint={field.description}
        rows={4}
        maxLength={field.maxLength}
        value={text}
        onChange={(e) => {
          onChange(e.target.value);
        }}
      />
    );
  }
  return (
    <TextInput
      {...common}
      hint={field.description}
      maxLength={field.maxLength}
      value={text}
      onChange={(e) => {
        onChange(e.target.value);
      }}
    />
  );
}
