import type { IncidentPatch } from '@/api/types';

export type Patch = Omit<IncidentPatch, 'expected_version'>;

/**
 * Sayfanın formlara verdiği kaydetme işlevi. 409 sürüm çakışmasını sayfa yakalar ve
 * "kayıt değişti" penceresini açar ('conflict'); diğer hatalar formda gösterilir.
 */
export type SaveFn = (version: number, patch: Patch, fieldNames: string[]) => Promise<SaveResult>;

export type SaveResult =
  | { ok: true }
  | { ok: false; conflict: true }
  | { ok: false; conflict: false; error: unknown };
