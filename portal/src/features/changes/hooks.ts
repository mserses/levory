import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { changesApi, cmdbApi, queryKeys } from '@/api';
import type {
  Change,
  ChangeApprovalCreate,
  ChangeCreate,
  ChangeListQuery,
  ChangeTransition,
} from '@/api/types';

export function useChangeList(query: ChangeListQuery) {
  return useQuery({
    queryKey: queryKeys.changes.list(query),
    queryFn: ({ signal }) => changesApi.list(query, signal),
    placeholderData: (previous) => previous,
  });
}

export function useChange(id: string) {
  return useQuery({
    queryKey: queryKeys.changes.detail(id),
    queryFn: ({ signal }) => changesApi.get(id, signal),
  });
}

export function useChangeApprovals(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.changes.approvals(id),
    queryFn: ({ signal }) => changesApi.approvals(id, signal),
    enabled,
  });
}

export function useChangeDecisions(id: string) {
  return useQuery({
    queryKey: queryKeys.changes.decisions(id),
    queryFn: ({ signal }) => changesApi.decisions(id, signal),
  });
}

export function useChangeImpact(id: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.changes.impact(id),
    queryFn: ({ signal }) => changesApi.impact(id, signal),
    enabled,
  });
}

export function useChangeConflicts(id: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.changes.conflicts(id),
    queryFn: ({ signal }) => changesApi.conflicts(id, signal),
    enabled,
  });
}

export function useCalendar(from: Date, to: Date, enabled = true) {
  return useQuery({
    queryKey: queryKeys.changes.calendar(from.toISOString(), to.toISOString()),
    queryFn: ({ signal }) => changesApi.calendar(from, to, signal),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useTemplates(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.changes.templates,
    queryFn: ({ signal }) => changesApi.templates(true, signal),
    enabled,
  });
}

/** Seçili CI'lar için canlı etki önizlemesi (POST /cmdb/impact) */
export function useImpactPreview(ciIds: readonly string[]) {
  return useQuery({
    queryKey: queryKeys.cmdb.impactFor(ciIds, 5),
    queryFn: ({ signal }) => cmdbApi.impactFor(ciIds, 5, signal),
    enabled: ciIds.length > 0,
    placeholderData: (previous) => previous,
  });
}

async function refreshChange(
  queryClient: ReturnType<typeof useQueryClient>,
  id: string,
  updated?: Change,
) {
  if (updated) queryClient.setQueryData(queryKeys.changes.detail(id), updated);
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.changes.detail(id) }),
    queryClient.invalidateQueries({ queryKey: queryKeys.changes.lists }),
    queryClient.invalidateQueries({ queryKey: ['changes', 'calendar'] }),
  ]);
}

export function useCreateChange() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ChangeCreate) => changesApi.create(body),
    onSuccess: async (created) => {
      queryClient.setQueryData(queryKeys.changes.detail(created.id), created);
      await queryClient.invalidateQueries({ queryKey: queryKeys.changes.lists });
    },
  });
}

export interface TransitionVariables {
  /** Ekranın dayandığı sürüm (If-Match) */
  version: number;
  body: ChangeTransition;
}

/** Durum geçişi; 409 (sürüm ya da zamanlama çakışması) çağırana fırlar */
export function useTransition(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ version, body }: TransitionVariables) =>
      changesApi.transition(id, version, body),
    onSuccess: (updated) => refreshChange(queryClient, id, updated),
  });
}

export function useVote(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ChangeApprovalCreate) => changesApi.vote(id, body),
    onSettled: () => refreshChange(queryClient, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.changes.approvals(id) }),
  });
}

export function useAssessRisk(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => changesApi.assess(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.changes.decisions(id) }),
        refreshChange(queryClient, id),
      ]);
    },
  });
}

export function useApproveRiskDecision(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (decisionId: string) => changesApi.approveDecision(decisionId),
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.changes.decisions(id) }),
        refreshChange(queryClient, id),
      ]);
    },
  });
}
