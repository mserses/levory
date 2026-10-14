/**
 * Self-servis formundaki "Hizmet" listesi. Kaynak: ortak taksonomi (`@/lib/taxonomy`,
 * backend `taxonomy.json`'dan üretilir). Bilinmeyen değer gönderilse bile backend kabul eder;
 * triage onu taksonomi dışı sayar.
 */
import { SERVICE_LABELS, taxonomyOptions } from '@/lib/taxonomy';

export { SERVICE_LABELS };

export const SERVICE_OPTIONS = taxonomyOptions('service');

export function isServiceKey(value: string): boolean {
  return Object.prototype.hasOwnProperty.call(SERVICE_LABELS, value);
}

/** Kayıttaki hizmet değerinin Türkçe adı */
export function serviceLabel(value: string | null | undefined): string {
  if (!value) return 'Belirtilmedi';
  return isServiceKey(value) ? (SERVICE_LABELS[value] ?? value) : value;
}
