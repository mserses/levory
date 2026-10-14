/** API hataları: her hata Türkçe, kullanıcıya gösterilebilir bir `detail` taşır. */

export interface FieldError {
  /** Alan yolu, örn. "title" veya "body.caller" */
  field: string;
  message: string;
}

const DEFAULT_MESSAGES: Record<number, string> = {
  0: 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edip yeniden deneyin.',
  400: 'İstek geçersiz.',
  401: 'Oturumunuzun süresi doldu. Lütfen yeniden giriş yapın.',
  403: 'Bu işlem için yetkiniz yok.',
  404: 'Kayıt bulunamadı.',
  409: 'Kayıt siz çalışırken değişti. Güncel hâlini yükleyip yeniden deneyin.',
  422: 'Gönderilen bilgiler doğrulanamadı.',
  429: 'Çok fazla istek gönderildi. Biraz bekleyip yeniden deneyin.',
  500: 'Sunucuda beklenmeyen bir hata oluştu.',
  502: 'Sunucuya şu anda ulaşılamıyor.',
  503: 'Hizmet geçici olarak kullanılamıyor.',
};

export function defaultMessage(status: number): string {
  return (
    DEFAULT_MESSAGES[status] ??
    (status >= 500 ? (DEFAULT_MESSAGES[500] ?? '') : 'İstek tamamlanamadı.')
  );
}

export class ApiError extends Error {
  override readonly name = 'ApiError';
  /** HTTP durum kodu; ağ hatası/zaman aşımı için 0 */
  readonly status: number;
  /** Kullanıcıya gösterilecek Türkçe açıklama (sunucu `detail` metni varsa o) */
  readonly detail: string;
  /** 422 doğrulama hataları (alan bazında) */
  readonly fieldErrors: FieldError[];
  /** Ham yanıt gövdesi (hata ayıklama için) */
  readonly body: unknown;

  constructor(status: number, detail: string, fieldErrors: FieldError[] = [], body?: unknown) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.fieldErrors = fieldErrors;
    this.body = body;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
  get isForbidden(): boolean {
    return this.status === 403;
  }
  get isNotFound(): boolean {
    return this.status === 404;
  }
  /** 409: sürüm çakışması ya da bayat yapay zekâ önerisi ("yeniden triage gerekli") */
  get isConflict(): boolean {
    return this.status === 409;
  }
  get isNetworkError(): boolean {
    return this.status === 0;
  }
  /** Bayat öneri onayı: kullanıcıya yeniden triage önerilmeli */
  get needsRetriage(): boolean {
    return this.status === 409 && /triage/i.test(this.detail);
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

interface PydanticIssue {
  loc?: unknown[];
  msg?: string;
}

function isIssueList(value: unknown): value is PydanticIssue[] {
  return Array.isArray(value) && value.every((v) => typeof v === 'object' && v !== null);
}

function isMessageDetail(value: unknown): value is { message: string; errors?: unknown[] } {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const v = value as { message?: unknown; errors?: unknown };
  return (
    typeof v.message === 'string' &&
    v.message.trim() !== '' &&
    (v.errors === undefined || Array.isArray(v.errors))
  );
}

/** FastAPI hata gövdesinden (detail: string | ValidationError[]) ApiError üretir. */
export function errorFromBody(status: number, body: unknown): ApiError {
  const detail =
    typeof body === 'object' && body !== null ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === 'string' && detail.trim()) {
    return new ApiError(status, detail, [], body);
  }
  if (isMessageDetail(detail)) {
    // Katalog/talep doğrulaması: {"message": "...", "errors": ["...", ...]}
    const fieldErrors = (detail.errors ?? [])
      .filter((e): e is string => typeof e === 'string')
      .map((message) => ({ field: '', message }));
    return new ApiError(status, detail.message, fieldErrors, body);
  }
  if (isIssueList(detail)) {
    const fieldErrors = detail.map((issue) => {
      const loc = (issue.loc ?? []).filter((p) => p !== 'body' && p !== 'query' && p !== 'path');
      return { field: loc.map(String).join('.'), message: issue.msg ?? 'Geçersiz değer' };
    });
    const summary = fieldErrors
      .map((f) => (f.field ? `${f.field}: ${f.message}` : f.message))
      .join('; ');
    return new ApiError(
      status,
      summary ? `${defaultMessage(status)} (${summary})` : defaultMessage(status),
      fieldErrors,
      body,
    );
  }
  return new ApiError(status, defaultMessage(status), [], body);
}

/** Herhangi bir hatayı kullanıcıya gösterilecek Türkçe metne çevirir. */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.detail;
  return 'Beklenmeyen bir hata oluştu.';
}
