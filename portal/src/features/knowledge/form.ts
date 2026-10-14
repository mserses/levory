/** Makale formu: değerler, doğrulama ve API gövdesine dönüştürme (saf fonksiyonlar). */
import type { ApiError } from '@/api';
import type { Article, ArticleCreate, ArticleUpdate } from '@/api/types';

export interface ArticleFormValues {
  title: string;
  body: string;
  /** '' = kategorisiz */
  category: string;
  isKnownError: boolean;
  workaround: string;
}

export type ArticleFormErrors = Partial<Record<keyof ArticleFormValues, string>>;

export const TITLE_MAX = 300;

export function valuesFromArticle(article?: Article | null): ArticleFormValues {
  return {
    title: article?.title ?? '',
    body: article?.body ?? '',
    category: article?.category ?? '',
    isKnownError: article?.is_known_error ?? false,
    workaround: article?.workaround ?? '',
  };
}

export function validateArticle(v: ArticleFormValues): ArticleFormErrors {
  const errors: ArticleFormErrors = {};
  const title = v.title.trim();
  if (!title) errors.title = 'Başlık zorunludur.';
  else if (title.length > TITLE_MAX)
    errors.title = `Başlık en fazla ${TITLE_MAX} karakter olabilir.`;
  if (!v.body.trim()) errors.body = 'İçerik zorunludur.';
  if (v.isKnownError && !v.workaround.trim())
    errors.workaround = 'Bilinen hata kayıtları için geçici çözümü yazın.';
  return errors;
}

/** Gövde metninde satır sonları korunur; yalnızca baş/son boşluk kırpılır. */
const clean = (s: string) => s.replace(/\r\n/g, '\n').trim();

export function toCreateBody(v: ArticleFormValues, publish: boolean): ArticleCreate {
  return {
    title: v.title.trim(),
    body: clean(v.body),
    category: v.category || null,
    is_known_error: v.isKnownError,
    workaround: v.isKnownError ? clean(v.workaround) || null : null,
    state: publish ? 'published' : 'draft',
  };
}

/** Yalnızca değişen alanlar. Boş nesne = değişiklik yok. */
export function toUpdateBody(article: Article, v: ArticleFormValues): ArticleUpdate {
  const next = toCreateBody(v, false);
  const patch: ArticleUpdate = {};
  if (next.title !== article.title) patch.title = next.title;
  if (next.body !== article.body) patch.body = next.body;
  if ((next.category ?? null) !== article.category) patch.category = next.category ?? null;
  if (next.is_known_error !== article.is_known_error) patch.is_known_error = next.is_known_error;
  if ((next.workaround ?? null) !== article.workaround) patch.workaround = next.workaround ?? null;
  return patch;
}

const SERVER_FIELDS: Record<string, keyof ArticleFormValues> = {
  title: 'title',
  body: 'body',
  category: 'category',
  is_known_error: 'isKnownError',
  workaround: 'workaround',
};

/** 422 alan hatalarını form alanlarına eşler */
export function serverFieldErrors(error: ApiError): ArticleFormErrors {
  const out: ArticleFormErrors = {};
  for (const fe of error.fieldErrors) {
    const key = SERVER_FIELDS[fe.field.split('.').pop() ?? ''];
    if (key && !out[key]) out[key] = fe.message;
  }
  return out;
}
