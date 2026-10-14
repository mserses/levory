/**
 * İnce fetch sarmalayıcısı. Tüm API çağrıları buradan geçer:
 * - taban adres `config.apiBaseUrl` (VITE_API_BASE_URL)
 * - `Authorization: Bearer <token>` (AuthProvider `configureApiClient` ile bağlar)
 * - JSON hata gövdesi -> `ApiError` (status + Türkçe detail)
 * - `ifMatch` ile iyimser kilit (`If-Match: "<version>"`); 409 çağırana fırlatılır
 * - 401 -> `onUnauthorized` (yeniden giriş) + ApiError fırlatılır
 */
import { config } from '@/config/env';
import { ApiError, errorFromBody } from './errors';

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue | readonly QueryValue[]>;

export interface RequestOptions {
  query?: QueryParams | undefined;
  body?: unknown;
  /** Olay sürümü (IncidentRead.version) ya da ETag; `If-Match` başlığı olarak gönderilir */
  ifMatch?: number | string | undefined;
  signal?: AbortSignal | undefined;
  headers?: Record<string, string>;
}

export interface ApiResponse<T> {
  data: T;
  status: number;
  /** Yanıttaki ETag (örn. '"3"'); yoksa null */
  etag: string | null;
}

interface ClientHooks {
  getAccessToken: () => string | null | Promise<string | null>;
  onUnauthorized: () => void;
}

let hooks: ClientHooks = {
  getAccessToken: () => null,
  onUnauthorized: () => undefined,
};

/** AuthProvider tarafından çağrılır; testlerde de belirteç vermek için kullanılabilir. */
export function configureApiClient(next: Partial<ClientHooks>): void {
  hooks = { ...hooks, ...next };
}

export function buildUrl(path: string, query?: QueryParams): string {
  const url = new URL(config.apiBaseUrl + (path.startsWith('/') ? path : `/${path}`));
  if (query) {
    for (const [key, raw] of Object.entries(query)) {
      const values = Array.isArray(raw) ? (raw as readonly QueryValue[]) : [raw as QueryValue];
      for (const value of values) {
        if (value === undefined || value === null || value === '') continue;
        url.searchParams.append(key, String(value));
      }
    }
  }
  return url.toString();
}

function formatIfMatch(value: number | string): string {
  return typeof value === 'number' ? `"${value}"` : value;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  const type = response.headers.get('content-type') ?? '';
  if (type.includes('json')) {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return text;
    }
  }
  return text;
}

export async function requestWithMeta<T>(
  method: string,
  path: string,
  options: RequestOptions = {},
): Promise<ApiResponse<T>> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers };
  const token = await hooks.getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.ifMatch !== undefined) headers['If-Match'] = formatIfMatch(options.ifMatch);

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : null,
      signal: options.signal ?? null,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edip yeniden deneyin.');
  }

  const body = await parseBody(response);
  if (!response.ok) {
    const error = errorFromBody(response.status, body);
    if (response.status === 401) hooks.onUnauthorized();
    throw error;
  }
  return { data: body as T, status: response.status, etag: response.headers.get('etag') };
}

export async function request<T>(
  method: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  return (await requestWithMeta<T>(method, path, options)).data;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('POST', path, { ...options, body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PATCH', path, { ...options, body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>('PUT', path, { ...options, body }),
  delete: <T>(path: string, options?: RequestOptions) => request<T>('DELETE', path, options),
};
