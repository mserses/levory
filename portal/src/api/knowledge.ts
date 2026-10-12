import { api } from './client';
import type {
  Article,
  ArticleCreate,
  ArticleListQuery,
  ArticleSearchQuery,
  ArticleUpdate,
  SearchResponse,
} from './types';

export const knowledgeApi = {
  list: (query: ArticleListQuery = {}, signal?: AbortSignal) =>
    api.get<Article[]>('/knowledge/articles', { query, signal }),
  get: (id: string, signal?: AbortSignal) =>
    api.get<Article>(`/knowledge/articles/${encodeURIComponent(id)}`, { signal }),
  search: (query: ArticleSearchQuery, signal?: AbortSignal) =>
    api.get<SearchResponse>('/knowledge/search', { query, signal }),
  create: (body: ArticleCreate) => api.post<Article>('/knowledge/articles', body),
  update: (id: string, body: ArticleUpdate) =>
    api.patch<Article>(`/knowledge/articles/${encodeURIComponent(id)}`, body),
  publish: (id: string) =>
    api.post<Article>(`/knowledge/articles/${encodeURIComponent(id)}/publish`),
  retire: (id: string) => api.post<Article>(`/knowledge/articles/${encodeURIComponent(id)}/retire`),
};
