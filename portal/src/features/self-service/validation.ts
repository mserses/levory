/**
 * Yeni talep formu doğrulaması (istemci tarafı). Asıl doğrulama backend'dedir;
 * buradaki sınırlar backend'le uyumludur (başlık ≤ 300, hizmet taksonomiden).
 */
import { z } from 'zod';
import type { Urgency } from '@/api';
import { isServiceKey } from './services';

const URGENCIES: readonly string[] = ['low', 'medium', 'high'] satisfies Urgency[];
const isUrgency = (v: string): v is Urgency => URGENCIES.includes(v);

export const TITLE_MIN = 5;
export const TITLE_MAX = 300;
export const DESCRIPTION_MIN = 10;
export const DESCRIPTION_MAX = 5000;

/**
 * "parola: abc123", "password=...", "şifrem: ..." gibi açıkça paylaşılmış parolaları yakalar.
 * "Şifremi unuttum" gibi masum ifadelere takılmaz (ayırıcı + değer gerekir).
 */
const SECRET_PATTERN =
  /(?:şifre(?:m|si|niz)?|sifre(?:m|si|niz)?|parola(?:m|sı|nız)?|password|passwd|pwd|pin)\s*[:=]\s*\S+/iu;

export function looksLikeSecret(text: string): boolean {
  return SECRET_PATTERN.test(text);
}

export const SECRET_MESSAGE =
  'Metinde bir parola paylaşılmış gibi görünüyor. Lütfen parolanızı silin; destek ekibi parolanızı asla istemez.';

const noSecret = (value: string) => !looksLikeSecret(value);

export const newRequestSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, 'Kısa bir başlık yazın.')
    .min(TITLE_MIN, `Başlık en az ${TITLE_MIN} karakter olmalı.`)
    .max(TITLE_MAX, `Başlık en fazla ${TITLE_MAX} karakter olabilir.`)
    .refine(noSecret, SECRET_MESSAGE),
  description: z
    .string()
    .trim()
    .min(1, 'Sorunu veya talebinizi açıklayın.')
    .min(DESCRIPTION_MIN, `Açıklama en az ${DESCRIPTION_MIN} karakter olmalı.`)
    .max(DESCRIPTION_MAX, `Açıklama en fazla ${DESCRIPTION_MAX} karakter olabilir.`)
    .refine(noSecret, SECRET_MESSAGE),
  service: z
    .string()
    .refine((v) => v === '' || isServiceKey(v), 'Listeden bir hizmet seçin.')
    .transform((v) => (v === '' ? null : v)),
  urgency: z
    .string()
    .refine((v) => v === '' || isUrgency(v), 'Listeden bir aciliyet seçin.')
    .transform((v) => (isUrgency(v) ? v : null)),
});

export type NewRequestInput = z.input<typeof newRequestSchema>;
export type NewRequestValues = z.output<typeof newRequestSchema>;
export type NewRequestField = keyof NewRequestInput;
export type FormErrors = Partial<Record<NewRequestField, string>>;

export type ValidationResult =
  | { ok: true; values: NewRequestValues }
  | { ok: false; errors: FormErrors };

export function validateNewRequest(input: NewRequestInput): ValidationResult {
  const parsed = newRequestSchema.safeParse(input);
  if (parsed.success) return { ok: true, values: parsed.data };
  const errors: FormErrors = {};
  for (const issue of parsed.error.issues) {
    const field = issue.path[0];
    if (
      field === 'title' ||
      field === 'description' ||
      field === 'service' ||
      field === 'urgency'
    ) {
      errors[field] ??= issue.message;
    }
  }
  return { ok: false, errors };
}
