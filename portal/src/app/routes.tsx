/**
 * Rota tablosu. Ekranlar kendi özellik klasörlerinde (src/features/<alan>/) yaşar ve
 * varsayılan dışa aktarımla tembel yüklenir. Erişim rolleri navigation.ts'deki ROUTE_ROLES'tan.
 */
import { lazy, Suspense, type ReactNode } from 'react';
import { Route, Routes } from 'react-router-dom';
import { RequireRole } from '@/auth/RequireRole';
import type { Role } from '@/auth/roles';
import { Spinner } from '@/components/Spinner';
import { AppShell } from './AppShell';
import { ROUTE_ROLES } from './navigation';
import { AuthCallbackPage } from './pages/AuthCallbackPage';
import { ForbiddenPage } from './pages/ForbiddenPage';
import { HomeRedirect } from './pages/HomeRedirect';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';

const NewRequestPage = lazy(() => import('@/features/self-service/NewRequestPage'));
const MyRequestsPage = lazy(() => import('@/features/self-service/MyRequestsPage'));
const IncidentQueuePage = lazy(() => import('@/features/incidents/IncidentQueuePage'));
const IncidentDetailPage = lazy(() => import('@/features/incidents/IncidentDetailPage'));
const ApprovalsPage = lazy(() => import('@/features/approvals/ApprovalsPage'));
const KnowledgeListPage = lazy(() => import('@/features/knowledge/KnowledgeListPage'));
const KnowledgeArticlePage = lazy(() => import('@/features/knowledge/KnowledgeArticlePage'));
const ChangeListPage = lazy(() => import('@/features/changes/ChangeListPage'));
const NewChangePage = lazy(() => import('@/features/changes/NewChangePage'));
const ChangeDetailPage = lazy(() => import('@/features/changes/ChangeDetailPage'));
const CmdbListPage = lazy(() => import('@/features/cmdb/CmdbListPage'));
const CIDetailPage = lazy(() => import('@/features/cmdb/CIDetailPage'));
const CatalogPage = lazy(() => import('@/features/catalog/CatalogPage'));
const CatalogItemPage = lazy(() => import('@/features/catalog/CatalogItemPage'));
const RequestDetailPage = lazy(() => import('@/features/requests/RequestDetailPage'));
const ProblemListPage = lazy(() => import('@/features/problems/ProblemListPage'));
const ProblemDetailPage = lazy(() => import('@/features/problems/ProblemDetailPage'));
const KpiDashboardPage = lazy(() => import('@/features/kpi/KpiDashboardPage'));
const SlaForecastPage = lazy(() => import('@/features/kpi/SlaForecastPage'));
const EventGroupListPage = lazy(() => import('@/features/events/EventGroupListPage'));
const EventGroupDetailPage = lazy(() => import('@/features/events/EventGroupDetailPage'));
const ImprovementListPage = lazy(() => import('@/features/improvements/ImprovementListPage'));
const ImprovementDetailPage = lazy(() => import('@/features/improvements/ImprovementDetailPage'));
const AuditPage = lazy(() => import('@/features/admin/AuditPage'));
const IntegrationsPage = lazy(() => import('@/features/admin/IntegrationsPage'));

function guard(roles: readonly Role[], element: ReactNode) {
  return (
    <RequireRole roles={roles}>
      <Suspense fallback={<Spinner block />}>{element}</Suspense>
    </RequireRole>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/giris" element={<LoginPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
      <Route
        element={
          <RequireRole roles={[]}>
            <AppShell />
          </RequireRole>
        }
      >
        <Route index element={<HomeRedirect />} />
        <Route path="talep" element={guard(ROUTE_ROLES.newRequest, <NewRequestPage />)} />
        <Route path="taleplerim" element={guard(ROUTE_ROLES.myRequests, <MyRequestsPage />)} />
        <Route
          path="taleplerim/:id"
          element={guard(ROUTE_ROLES.requestDetail, <RequestDetailPage />)}
        />
        <Route path="katalog" element={guard(ROUTE_ROLES.catalog, <CatalogPage />)} />
        <Route path="katalog/:key" element={guard(ROUTE_ROLES.catalog, <CatalogItemPage />)} />
        <Route path="problemler" element={guard(ROUTE_ROLES.problems, <ProblemListPage />)} />
        <Route path="problemler/:id" element={guard(ROUTE_ROLES.problems, <ProblemDetailPage />)} />
        <Route path="kayitlar" element={guard(ROUTE_ROLES.incidents, <IncidentQueuePage />)} />
        <Route path="kayitlar/:id" element={guard(ROUTE_ROLES.incidents, <IncidentDetailPage />)} />
        <Route path="onaylar" element={guard(ROUTE_ROLES.approvals, <ApprovalsPage />)} />
        <Route path="bilgi-bankasi" element={guard(ROUTE_ROLES.knowledge, <KnowledgeListPage />)} />
        <Route
          path="bilgi-bankasi/:id"
          element={guard(ROUTE_ROLES.knowledge, <KnowledgeArticlePage />)}
        />
        <Route path="degisiklikler" element={guard(ROUTE_ROLES.changes, <ChangeListPage />)} />
        <Route
          path="degisiklikler/yeni"
          element={guard(ROUTE_ROLES.newChange, <NewChangePage />)}
        />
        <Route
          path="degisiklikler/:id"
          element={guard(ROUTE_ROLES.changes, <ChangeDetailPage />)}
        />
        <Route path="cmdb" element={guard(ROUTE_ROLES.cmdb, <CmdbListPage />)} />
        <Route path="cmdb/:id" element={guard(ROUTE_ROLES.cmdb, <CIDetailPage />)} />
        <Route path="panolar" element={guard(ROUTE_ROLES.dashboards, <KpiDashboardPage />)} />
        <Route path="sla-tahmin" element={guard(ROUTE_ROLES.slaForecast, <SlaForecastPage />)} />
        <Route path="olaylar" element={guard(ROUTE_ROLES.events, <EventGroupListPage />)} />
        <Route path="olaylar/:id" element={guard(ROUTE_ROLES.events, <EventGroupDetailPage />)} />
        <Route
          path="iyilestirmeler"
          element={guard(ROUTE_ROLES.improvements, <ImprovementListPage />)}
        />
        <Route
          path="iyilestirmeler/:id"
          element={guard(ROUTE_ROLES.improvements, <ImprovementDetailPage />)}
        />
        <Route path="yonetim/denetim" element={guard(ROUTE_ROLES.audit, <AuditPage />)} />
        <Route
          path="yonetim/entegrasyonlar"
          element={guard(ROUTE_ROLES.integrations, <IntegrationsPage />)}
        />
        <Route path="yetkisiz" element={<ForbiddenPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
