import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { knowledgeApi, queryKeys } from '@/api';
import type { Article, ArticleCreate, ArticleListQuery, ArticleUpdate } from '@/api/types';
import type { ArticleState } from './labels';

export const LIST_PAGE_SIZE = 20;
export const SEARCH_LIMIT = 20;

/**
 * Sayfalı liste. Backend toplam sayı döndürmediği için bir fazla kayıt istenir;
 * fazlalık varsa "sonraki sayfa" etkinleşir.
 */
export function useArticleList(state: ArticleState | null, page: number) {
  const query: ArticleListQuery = {
    state,
    limit: LIST_PAGE_SIZE + 1,
    offset: (page - 1) * LIST_PAGE_SIZE,
  };
  const result = useQuery({
    queryKey: queryKeys.knowledge.list(query),
    queryFn: ({ signal }) => knowledgeApi.list(query, signal),
  });
  const rows = result.data ?? [];
  return {
    ...result,
    articles: rows.slice(0, LIST_PAGE_SIZE),
    hasNext: rows.length > LIST_PAGE_SIZE,
  };
}

export function useArticleSearch(q: string) {
  const term = q.trim();
  return useQuery({
    queryKey: queryKeys.knowledge.search(term),
    queryFn: ({ signal }) => knowledgeApi.search({ q: term, limit: SEARCH_LIMIT }, signal),
    enabled: term.length > 0,
  });
}

export function useArticle(id: string) {
  return useQuery({
    queryKey: queryKeys.knowledge.detail(id),
    queryFn: ({ signal }) => knowledgeApi.get(id, signal),
    enabled: id.length > 0,
  });
}

function useStoreArticle() {
  const queryClient = useQueryClient();
  return (article: Article) => {
    queryClient.setQueryData(queryKeys.knowledge.detail(article.id), article);
    void queryClient.invalidateQueries({
      queryKey: queryKeys.knowledge.all,
      // Az önce yazılan ayrıntıyı yeniden çekmeye gerek yok
      predicate: (q) => q.queryKey[1] !== 'detail' || q.queryKey[2] !== article.id,
    });
  };
}

export function useCreateArticle() {
  const store = useStoreArticle();
  return useMutation({
    mutationFn: (body: ArticleCreate) => knowledgeApi.create(body),
    onSuccess: store,
  });
}

export function useUpdateArticle(id: string) {
  const store = useStoreArticle();
  return useMutation({
    mutationFn: (body: ArticleUpdate) => knowledgeApi.update(id, body),
    onSuccess: store,
  });
}

export type Transition = 'publish' | 'retire';

export function useArticleTransition(id: string) {
  const store = useStoreArticle();
  return useMutation({
    mutationFn: (t: Transition) =>
      t === 'publish' ? knowledgeApi.publish(id) : knowledgeApi.retire(id),
    onSuccess: store,
  });
}
