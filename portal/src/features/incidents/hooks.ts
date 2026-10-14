import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { incidentsApi, queryKeys } from '@/api';
import type { Incident, IncidentListQuery, IncidentPatch } from '@/api/types';

export function useIncidentList(query: IncidentListQuery) {
  return useQuery({
    queryKey: queryKeys.incidents.list(query),
    queryFn: ({ signal }) => incidentsApi.list(query, signal),
    placeholderData: (previous) => previous,
  });
}

export function useIncident(id: string) {
  return useQuery({
    queryKey: queryKeys.incidents.detail(id),
    queryFn: ({ signal }) => incidentsApi.get(id, signal),
  });
}

export function useIncidentDecisions(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.incidents.decisions(id),
    queryFn: ({ signal }) => incidentsApi.decisions(id, signal),
    enabled,
  });
}

export interface UpdateVariables {
  /** Formun dayandığı sürüm (If-Match); güncel önbellek sürümü DEĞİL */
  version: number;
  patch: Omit<IncidentPatch, 'expected_version'>;
}

/** Optimistik kilitli güncelleme. 409 çağırana fırlar; ekran "kayıt değişti" gösterir. */
export function useUpdateIncident(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ version, patch }: UpdateVariables) => incidentsApi.update(id, version, patch),
    onSuccess: async (updated: Incident) => {
      queryClient.setQueryData(queryKeys.incidents.detail(id), updated);
      // Sürüm değişti: onay kutusundaki öneriler bayat görünmeli
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.incidents.lists }),
        queryClient.invalidateQueries({ queryKey: queryKeys.decisions.all }),
      ]);
    },
  });
}

export function useTriage(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => incidentsApi.triage(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.incidents.detail(id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.incidents.lists }),
        queryClient.invalidateQueries({ queryKey: queryKeys.decisions.all }),
      ]);
    },
  });
}

/** SLA rozetleri için dakikada bir ilerleyen "şimdi" */
export function useNow(intervalMs = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => {
      setNow(new Date());
    }, intervalMs);
    return () => {
      window.clearInterval(t);
    };
  }, [intervalMs]);
  return now;
}
