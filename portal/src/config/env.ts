import { z } from 'zod';

/**
 * Derleme zamanı ortam değişkenleri (VITE_*). Tek kaynak burasıdır; kodda doğrudan
 * `import.meta.env` okumayın. VITE_ değerleri pakete gömülür: gizli bilgi koymayın.
 */
const EnvSchema = z
  .object({
    VITE_API_BASE_URL: z.string().url().default('http://localhost:8000'),
    VITE_AUTH_MODE: z.enum(['oidc', 'dev']).default('oidc'),
    VITE_OIDC_AUTHORITY: z.string().optional(),
    VITE_OIDC_CLIENT_ID: z.string().optional(),
    VITE_OIDC_SCOPE: z.string().default('openid profile email'),
    VITE_TIME_ZONE: z.string().default('Europe/Istanbul'),
  })
  .superRefine((env, ctx) => {
    if (env.VITE_AUTH_MODE === 'oidc') {
      if (!env.VITE_OIDC_AUTHORITY) {
        ctx.addIssue({
          code: 'custom',
          path: ['VITE_OIDC_AUTHORITY'],
          message: 'OIDC için zorunlu',
        });
      }
      if (!env.VITE_OIDC_CLIENT_ID) {
        ctx.addIssue({
          code: 'custom',
          path: ['VITE_OIDC_CLIENT_ID'],
          message: 'OIDC için zorunlu',
        });
      }
    }
  });

export interface AppConfig {
  apiBaseUrl: string;
  authMode: 'oidc' | 'dev';
  oidc: { authority: string; clientId: string; scope: string } | null;
  timeZone: string;
  /** Yapılandırma hatası varsa Türkçe açıklama; uygulama bunu ekranda gösterir. */
  error: string | null;
}

function emptyToUndefined(raw: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, v === '' ? undefined : v]));
}

export function parseConfig(raw: Record<string, unknown>): AppConfig {
  const result = EnvSchema.safeParse(emptyToUndefined(raw));
  if (!result.success) {
    const fallback = EnvSchema.innerType().parse({});
    return {
      apiBaseUrl: fallback.VITE_API_BASE_URL,
      authMode: 'dev',
      oidc: null,
      timeZone: fallback.VITE_TIME_ZONE,
      error:
        'Portal yapılandırması geçersiz: ' +
        result.error.issues.map((i) => `${i.path.join('.')} (${i.message})`).join(', '),
    };
  }
  const env = result.data;
  return {
    apiBaseUrl: env.VITE_API_BASE_URL.replace(/\/+$/, ''),
    authMode: env.VITE_AUTH_MODE,
    oidc:
      env.VITE_AUTH_MODE === 'oidc' && env.VITE_OIDC_AUTHORITY && env.VITE_OIDC_CLIENT_ID
        ? {
            authority: env.VITE_OIDC_AUTHORITY,
            clientId: env.VITE_OIDC_CLIENT_ID,
            scope: env.VITE_OIDC_SCOPE,
          }
        : null,
    timeZone: env.VITE_TIME_ZONE,
    error: null,
  };
}

export const config: AppConfig = parseConfig(import.meta.env as unknown as Record<string, unknown>);
