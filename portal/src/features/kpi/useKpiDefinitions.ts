import { useQuery } from '@tanstack/react-query';
import { kpiApi, queryKeys } from '@/api';

/** `GET /kpi/definitions` — KPI adı, birimi, iyi yönü (nadiren değişir; uzun önbellek) */
export function useKpiDefinitions() {
  return useQuery({
    queryKey: queryKeys.kpi.definitions,
    queryFn: ({ signal }) => kpiApi.definitions(signal),
    staleTime: 60 * 60_000,
  });
}
