import { api } from './client';
import type { Improvement, ImprovementListQuery } from './types';

const path = (id: string) => `/improvements/${encodeURIComponent(id)}`;

/**
 * Sürekli iyileştirme (CSI) kaydı (`api/improvements.py`, docs/faz4.md). Durum adımları sürümlüdür
 * (`If-Match`; çakışma 409). Onay / red / kapatma yalnızca approver / admin; başlatma, uygulandı
 * işareti ve ölçüm agent / approver / admin. Yapay zekâ aktörü durum değiştiremez (sunucu reddeder).
 */
export const improvementsApi = {
  list: (query: ImprovementListQuery = {}, signal?: AbortSignal) =>
    api.get<Improvement[]>('/improvements', { query, signal }),

  get: (id: string, signal?: AbortSignal) => api.get<Improvement>(path(id), { signal }),

  approve: (id: string, version: number, note?: string) =>
    api.post<Improvement>(`${path(id)}/approve`, { note: note ?? null }, { ifMatch: version }),

  /** Gerekçe zorunlu (boşsa 422) */
  reject: (id: string, version: number, note: string) =>
    api.post<Improvement>(`${path(id)}/reject`, { note }, { ifMatch: version }),

  start: (id: string, version: number, note?: string) =>
    api.post<Improvement>(`${path(id)}/start`, { note: note ?? null }, { ifMatch: version }),

  /** Uygulamanın bittiği an (boşsa şimdi); ölçüm penceresi buradan başlar */
  implemented: (id: string, version: number, at?: string) =>
    api.post<Improvement>(`${path(id)}/implemented`, { at: at ?? null }, { ifMatch: version }),

  /** Önce/sonra KPI ölçümü; pencere dolmadıysa 409, veri yoksa 422 */
  measure: (id: string, version: number) =>
    api.post<Improvement>(`${path(id)}/measure`, undefined, { ifMatch: version }),

  /** Ölçülen kaydı kapatır; KPI'sız kayıt uygulamadan kapanış notuyla */
  close: (id: string, version: number, note?: string) =>
    api.post<Improvement>(`${path(id)}/close`, { note: note ?? null }, { ifMatch: version }),
};
