import { api } from './client';
import type { CatalogItem, CatalogListQuery } from './types';

/** Hizmet kataloğu (Faz 3). Her oturum açmış kullanıcı etkin öğeleri görür. */
export const catalogApi = {
  list: (query: CatalogListQuery = {}, signal?: AbortSignal) =>
    api.get<CatalogItem[]>('/catalog/items', { query, signal }),

  /** Kimlik ya da anahtar (ör. `password_reset`) */
  get: (item: string, signal?: AbortSignal) =>
    api.get<CatalogItem>(`/catalog/items/${encodeURIComponent(item)}`, { signal }),
};
