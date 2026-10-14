import { api } from './client';
import type {
  ForecastQuery,
  ForecastReport,
  KPIDefinition,
  KPIReport,
  KPIReportQuery,
} from './types';

/**
 * Faz 4 KPI ve tahmine dayalı SLA uçları (`api/kpi.py`, docs/faz4.md). Salt okuma; roller agent /
 * approver / admin. Yüzde birimli KPI değerleri 0-100 aralığındadır, süreler saattir.
 */
export const kpiApi = {
  report: (query: KPIReportQuery = {}, signal?: AbortSignal) =>
    api.get<KPIReport>('/kpi', { query, signal }),

  definitions: (signal?: AbortSignal) => api.get<KPIDefinition[]>('/kpi/definitions', { signal }),

  slaReport: (query: Omit<KPIReportQuery, 'kpi'> = {}, signal?: AbortSignal) =>
    api.get<KPIReport>('/sla/report', { query, signal }),

  forecast: (query: ForecastQuery = {}, signal?: AbortSignal) =>
    api.get<ForecastReport>('/sla/forecast', { query, signal }),
};
