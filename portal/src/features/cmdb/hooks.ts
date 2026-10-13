import { useQueries, useQuery } from '@tanstack/react-query';
import { changesApi, cmdbApi, incidentsApi, queryKeys } from '@/api';
import type { CIListQuery } from '@/api/types';

export function useCIList(query: CIListQuery) {
  return useQuery({
    queryKey: queryKeys.cmdb.list(query),
    queryFn: ({ signal }) => cmdbApi.list(query, signal),
    placeholderData: (previous) => previous,
  });
}

export function useCI(id: string) {
  return useQuery({
    queryKey: queryKeys.cmdb.detail(id),
    queryFn: ({ signal }) => cmdbApi.get(id, signal),
  });
}

/** Birden çok CI'ı adlarıyla göstermek için (ilişki tablosu) */
export function useCIs(ids: readonly string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: queryKeys.cmdb.detail(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => cmdbApi.get(id, signal),
    })),
  });
}

export function useRelationships(id: string) {
  return useQuery({
    queryKey: queryKeys.cmdb.relationships(id),
    queryFn: ({ signal }) => cmdbApi.relationships(id, signal),
  });
}

export function useCIImpact(id: string, maxDepth: number) {
  return useQuery({
    queryKey: queryKeys.cmdb.impact(id, maxDepth),
    queryFn: ({ signal }) => cmdbApi.impact(id, maxDepth, signal),
    placeholderData: (previous) => previous,
  });
}

export const LINKED_CHANGES_LIMIT = 50;
/** Olay listesinde CI süzgeci yok (backend); son kayıtlar istemcide süzülür */
export const INCIDENT_SCAN_LIMIT = 500;

export function useLinkedChanges(ciId: string) {
  const query = { ci_id: ciId, limit: LINKED_CHANGES_LIMIT };
  return useQuery({
    queryKey: queryKeys.changes.list(query),
    queryFn: ({ signal }) => changesApi.list(query, signal),
  });
}

export function useLinkedIncidents(ciId: string, enabled: boolean) {
  const query = { limit: INCIDENT_SCAN_LIMIT };
  return useQuery({
    queryKey: queryKeys.incidents.list(query),
    queryFn: ({ signal }) => incidentsApi.list(query, signal),
    enabled,
    select: (incidents) => incidents.filter((i) => (i.ci_ids ?? []).includes(ciId)),
  });
}
