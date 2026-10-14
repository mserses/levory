import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { errorMessage, isApiError } from '@/api';
import type { Article } from '@/api/types';
import { Button, Dialog, Select, TextArea, TextInput, type SelectOption } from '@/components';
import {
  TITLE_MAX,
  serverFieldErrors,
  validateArticle,
  valuesFromArticle,
  type ArticleFormErrors,
  type ArticleFormValues,
} from '../form';
import { taxonomyOptions } from '@/lib/taxonomy';
import styles from '../Knowledge.module.css';

export interface ArticleFormDialogProps {
  open: boolean;
  /** Verilirse düzenleme kipi */
  article?: Article | null;
  busy: boolean;
  /** Son gönderim hatası (sunucu) */
  error: unknown;
  onSubmit: (values: ArticleFormValues, publish: boolean) => void;
  onClose: () => void;
}

const categoryOptions = (current: string): SelectOption[] => taxonomyOptions('category', current);

export function ArticleFormDialog({
  open,
  article,
  busy,
  error,
  onSubmit,
  onClose,
}: ArticleFormDialogProps) {
  const formId = useId();
  const editing = Boolean(article);
  const [values, setValues] = useState<ArticleFormValues>(() => valuesFromArticle(article));
  const [errors, setErrors] = useState<ArticleFormErrors>({});
  const publishRef = useRef(false);

  // Dialog her açılışta güncel makaleyle başlar (açıkken arka plan tazelemesi formu ezmez)
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) {
      setValues(valuesFromArticle(article));
      setErrors({});
    }
    wasOpen.current = open;
  }, [open, article]);

  useEffect(() => {
    if (isApiError(error) && error.fieldErrors.length > 0) setErrors(serverFieldErrors(error));
  }, [error]);

  const set = <K extends keyof ArticleFormValues>(key: K, value: ArticleFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = validateArticle(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      const first = Object.keys(found)[0];
      document.getElementById(`${formId}-${first ?? ''}`)?.focus();
      return;
    }
    onSubmit(values, publishRef.current);
  };

  const showServerError =
    error !== null && error !== undefined && !(isApiError(error) && error.fieldErrors.length > 0);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      dismissible={!busy}
      title={editing ? 'Makaleyi düzenle' : 'Yeni makale'}
      description={
        editing
          ? `${article?.number ?? ''} · Değişiklikler kaydedildiğinde hemen geçerli olur.`
          : 'Makale önce taslak olarak kaydedilebilir; yayınlanan makaleler aramada ve yapay zekâ önerilerinde kullanılır.'
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Vazgeç
          </Button>
          {editing ? (
            <Button type="submit" form={formId} loading={busy}>
              Kaydet
            </Button>
          ) : (
            <>
              <Button
                type="submit"
                form={formId}
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  publishRef.current = false;
                }}
              >
                Taslak olarak kaydet
              </Button>
              <Button
                type="submit"
                form={formId}
                loading={busy}
                onClick={() => {
                  publishRef.current = true;
                }}
              >
                Kaydet ve yayınla
              </Button>
            </>
          )}
        </>
      }
    >
      <form id={formId} className={styles.form} onSubmit={submit} noValidate>
        {showServerError && (
          <p className={styles.formError} role="alert">
            {errorMessage(error)}
          </p>
        )}
        <TextInput
          id={`${formId}-title`}
          label="Başlık"
          required
          maxLength={TITLE_MAX}
          value={values.title}
          error={errors.title}
          onChange={(e) => {
            set('title', e.target.value);
          }}
          data-autofocus
        />
        <Select
          id={`${formId}-category`}
          label="Kategori"
          placeholder="Kategorisiz"
          options={categoryOptions(values.category)}
          value={values.category}
          error={errors.category}
          onChange={(e) => {
            set('category', e.target.value);
          }}
        />
        <TextArea
          id={`${formId}-body`}
          label="İçerik"
          hint="Düz metin. Satır sonları olduğu gibi korunur; HTML/Markdown yorumlanmaz."
          required
          rows={10}
          value={values.body}
          error={errors.body}
          onChange={(e) => {
            set('body', e.target.value);
          }}
        />
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={values.isKnownError}
            onChange={(e) => {
              set('isKnownError', e.target.checked);
            }}
          />
          Bilinen hata (KEDB) — kök neden henüz giderilmedi
        </label>
        {values.isKnownError && (
          <TextArea
            id={`${formId}-workaround`}
            label="Geçici çözüm"
            hint="Kullanıcıyı kalıcı çözüm gelene kadar çalışır hâle getiren adımlar."
            required
            rows={5}
            value={values.workaround}
            error={errors.workaround}
            onChange={(e) => {
              set('workaround', e.target.value);
            }}
          />
        )}
      </form>
    </Dialog>
  );
}
