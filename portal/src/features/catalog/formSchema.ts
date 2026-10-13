/**
 * Katalog öğesi form şeması (JSON şemasının küçük alt kümesi; backend `services/catalog.py`):
 * metin (`string`, `minLength`/`maxLength`), seçenek (`enum`), evet/hayır (`boolean`), kullanıcı seçici
 * (`format: "user"`), grup seçici (`format: "group"`). İstemci doğrulaması backend kurallarının
 * aynısıdır; asıl doğrulama sunucudadır (422).
 */

export type FormFieldKind = 'text' | 'enum' | 'boolean' | 'user' | 'group';

export interface FormField {
  name: string;
  kind: FormFieldKind;
  title: string;
  description?: string;
  required: boolean;
  options?: string[];
  defaultValue?: string | boolean;
  minLength?: number;
  maxLength?: number;
  /** Uzun metin alanı (textarea) */
  multiline?: boolean;
}

export type FormValues = Record<string, string | boolean>;
export type FormErrors = Record<string, string>;

/** Backend varsayılanları */
export const DEFAULT_MAX_LENGTH = 500;
export const PICKER_MAX_LENGTH = 200;
/** Bu uzunluktan uzun metin alanları çok satırlı gösterilir */
const MULTILINE_FROM = 200;
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\u0000-\u001f\u007f]/;

/** Katalogdaki bilinen seçenek değerlerinin Türkçe adları; bilinmeyen değer olduğu gibi gösterilir */
const ENUM_LABELS: Record<string, string> = {
  full_access: 'Tam erişim',
  send_as: 'Farklı gönder (Send As)',
  send_on_behalf: 'Adına gönder',
  read_only: 'Salt okuma',
  SPE_E3: 'Microsoft 365 E3',
  SPE_E5: 'Microsoft 365 E5',
  POWER_BI_PRO: 'Power BI Pro',
};

export function enumOptionLabel(value: string): string {
  return ENUM_LABELS[value] ?? value;
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined);
const int = (v: unknown): number | undefined =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : undefined;

/** Şemayı alan listesine çevirir; desteklenmeyen alanlar `unsupported`'a düşer (gösterilmez). */
export function parseFormSchema(schema: unknown): { fields: FormField[]; unsupported: string[] } {
  const fields: FormField[] = [];
  const unsupported: string[] = [];
  if (!isRecord(schema) || !isRecord(schema.properties)) return { fields, unsupported };
  const required = new Set(
    Array.isArray(schema.required)
      ? schema.required.filter((r): r is string => typeof r === 'string')
      : [],
  );
  for (const [name, spec] of Object.entries(schema.properties)) {
    if (!isRecord(spec)) {
      unsupported.push(name);
      continue;
    }
    const base = {
      name,
      title: str(spec.title) ?? name,
      required: required.has(name),
      ...(str(spec.description) ? { description: str(spec.description) } : {}),
    };
    if (spec.type === 'boolean') {
      fields.push({
        ...base,
        kind: 'boolean',
        ...(typeof spec.default === 'boolean' ? { defaultValue: spec.default } : {}),
      });
      continue;
    }
    if (spec.type !== 'string') {
      unsupported.push(name);
      continue;
    }
    const format = spec.format;
    const options = Array.isArray(spec.enum)
      ? spec.enum.filter((e): e is string => typeof e === 'string' && e !== '')
      : undefined;
    const kind: FormFieldKind =
      format === 'user' ? 'user' : format === 'group' ? 'group' : options ? 'enum' : 'text';
    const maxLength =
      int(spec.maxLength) ?? (kind === 'user' || kind === 'group' ? PICKER_MAX_LENGTH : undefined);
    const minLength = int(spec.minLength);
    fields.push({
      ...base,
      kind,
      ...(options && kind === 'enum' ? { options } : {}),
      ...(str(spec.default) !== undefined ? { defaultValue: str(spec.default) } : {}),
      ...(maxLength !== undefined ? { maxLength } : {}),
      ...(minLength !== undefined ? { minLength } : {}),
      ...(kind === 'text' && (maxLength ?? DEFAULT_MAX_LENGTH) > MULTILINE_FROM
        ? { multiline: true }
        : {}),
    });
  }
  return { fields, unsupported };
}

export function initialValues(fields: readonly FormField[]): FormValues {
  const values: FormValues = {};
  for (const f of fields) {
    if (f.kind === 'boolean') values[f.name] = f.defaultValue === true;
    else values[f.name] = typeof f.defaultValue === 'string' ? f.defaultValue : '';
  }
  return values;
}

/** Backend `validate_form` ile aynı kurallar; Türkçe, alan başına tek mesaj */
export function validateForm(fields: readonly FormField[], values: FormValues): FormErrors {
  const errors: FormErrors = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (f.kind === 'boolean') {
      if (f.required && f.defaultValue === undefined && raw !== true) {
        errors[f.name] = `Devam etmek için “${f.title}” işaretlenmeli.`;
      }
      continue;
    }
    const value = typeof raw === 'string' ? raw.trim() : '';
    if (!value) {
      if (f.required) errors[f.name] = `${f.title} zorunludur.`;
      continue;
    }
    const max = f.maxLength ?? DEFAULT_MAX_LENGTH;
    if (value.length > max) {
      errors[f.name] = `${f.title} en fazla ${String(max)} karakter olabilir.`;
    } else if (f.minLength !== undefined && value.length < f.minLength) {
      errors[f.name] = `${f.title} en az ${String(f.minLength)} karakter olmalı.`;
    } else if ((f.kind === 'user' || f.kind === 'group') && CONTROL_RE.test(value)) {
      errors[f.name] = `${f.title} geçersiz karakter içeriyor.`;
    } else if (f.kind === 'enum' && !(f.options ?? []).includes(value)) {
      errors[f.name] = `${f.title} için listeden bir değer seçin.`;
    }
  }
  return errors;
}

/** Gönderilecek `form_values`: metinler kırpılır, boş metinler atlanır, boolean her zaman gider */
export function toPayload(fields: readonly FormField[], values: FormValues): FormValues {
  const out: FormValues = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (f.kind === 'boolean') {
      out[f.name] = raw === true;
      continue;
    }
    const value = typeof raw === 'string' ? raw.trim() : '';
    if (value) out[f.name] = value;
  }
  return out;
}
