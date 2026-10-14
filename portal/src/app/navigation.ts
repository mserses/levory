/**
 * Rota erişim haritası — gezinme menüsü ve rota korumaları BURADAN beslenir.
 * Yeni ekran eklerken önce buraya ekleyin; roller backend uç nokta rolleriyle uyumlu olmalı.
 */
import { hasAnyRole, isRequesterOnly, type Role } from '@/auth/roles';

/** Kenar çubuğu menü grupları (sıra = menüdeki sıra) */
export const NAV_GROUPS = [
  { id: 'selfService', label: 'Self-servis' },
  { id: 'operations', label: 'Hizmet operasyonu' },
  { id: 'changes', label: 'Değişiklik ve yapılandırma' },
  { id: 'knowledge', label: 'Bilgi' },
  { id: 'performance', label: 'Performans' },
  { id: 'admin', label: 'Yönetim' },
] as const;

export type NavGroupId = (typeof NAV_GROUPS)[number]['id'];

export interface NavItem {
  path: string;
  label: string;
  roles: readonly Role[];
  group: NavGroupId;
}

export interface NavGroup {
  id: NavGroupId;
  label: string;
  items: NavItem[];
}

export const ROUTE_ROLES = {
  newRequest: ['requester', 'agent'], // POST /incidents
  myRequests: ['requester', 'agent', 'approver'], // GET /incidents?mine=true
  incidents: ['agent', 'approver'], // GET /incidents (tümü)
  // GET /incidents/decisions (approver) + GET /actions (ActionApproverUser: approver/change_manager)
  approvals: ['approver', 'change_manager'],
  catalog: ['requester', 'agent'], // GET /catalog/items + POST /requests (CreatorUser)
  requestDetail: ['requester', 'agent', 'approver'], // GET /requests/{id} (IncidentViewerUser)
  problems: ['agent', 'approver'], // /problems/* (ProblemUser)
  knowledge: ['agent', 'approver'], // GET /knowledge/*
  changes: ['agent', 'change_manager', 'cab'], // GET /changes, /changes/{id} (ChangeViewerUser)
  newChange: ['agent', 'change_manager'], // POST /changes (ChangeCreatorUser)
  cmdb: ['agent', 'change_manager', 'cab'], // GET /cmdb/* (CMDBReaderUser)
  // Faz 4
  dashboards: ['agent', 'approver'], // GET /kpi (ReaderUser)
  slaForecast: ['agent', 'approver'], // GET /sla/forecast (ReaderUser)
  events: ['agent'], // GET /events/groups (AgentUser); üstlen/sustur = event.* araçları
  improvements: ['agent', 'approver'], // /improvements (ReaderUser; onay/red/kapatma ApproverUser)
  audit: ['admin'], // GET /audit
  integrations: ['admin'], // /integrations/*
} as const satisfies Record<string, readonly Role[]>;

export const NAV_ITEMS: readonly NavItem[] = [
  { path: '/talep', label: 'Yeni talep', roles: ROUTE_ROLES.newRequest, group: 'selfService' },
  { path: '/katalog', label: 'Hizmet kataloğu', roles: ROUTE_ROLES.catalog, group: 'selfService' },
  { path: '/taleplerim', label: 'Taleplerim', roles: ROUTE_ROLES.myRequests, group: 'selfService' },
  { path: '/kayitlar', label: 'Kayıtlar', roles: ROUTE_ROLES.incidents, group: 'operations' },
  { path: '/problemler', label: 'Sorunlar', roles: ROUTE_ROLES.problems, group: 'operations' },
  { path: '/olaylar', label: 'Olaylar', roles: ROUTE_ROLES.events, group: 'operations' },
  { path: '/onaylar', label: 'Onaylar', roles: ROUTE_ROLES.approvals, group: 'operations' },
  { path: '/degisiklikler', label: 'Değişiklikler', roles: ROUTE_ROLES.changes, group: 'changes' },
  { path: '/cmdb', label: 'CMDB', roles: ROUTE_ROLES.cmdb, group: 'changes' },
  {
    path: '/bilgi-bankasi',
    label: 'Bilgi bankası',
    roles: ROUTE_ROLES.knowledge,
    group: 'knowledge',
  },
  { path: '/panolar', label: 'Panolar', roles: ROUTE_ROLES.dashboards, group: 'performance' },
  {
    path: '/sla-tahmin',
    label: 'SLA tahmini',
    roles: ROUTE_ROLES.slaForecast,
    group: 'performance',
  },
  {
    path: '/iyilestirmeler',
    label: 'İyileştirmeler',
    roles: ROUTE_ROLES.improvements,
    group: 'performance',
  },
  { path: '/yonetim/denetim', label: 'Denetim', roles: ROUTE_ROLES.audit, group: 'admin' },
  {
    path: '/yonetim/entegrasyonlar',
    label: 'Entegrasyonlar',
    roles: ROUTE_ROLES.integrations,
    group: 'admin',
  },
];

export function navItemsFor(roles: readonly string[]): NavItem[] {
  return NAV_ITEMS.filter((item) => hasAnyRole(roles, item.roles));
}

/** Rolün görebildiği menü öğeleri, gruplanmış; boş gruplar gizlenir. */
export function navGroupsFor(roles: readonly string[]): NavGroup[] {
  const items = navItemsFor(roles);
  return NAV_GROUPS.map((g) => ({
    id: g.id,
    label: g.label,
    items: items.filter((i) => i.group === g.id),
  })).filter((g) => g.items.length > 0);
}

/** Girişten sonra kullanıcının açılış sayfası */
export function homePathFor(roles: readonly string[]): string {
  if (isRequesterOnly(roles)) {
    // Olaylarda self-servis; değişiklik rolü (cab/change_manager) varsa değişikliklere açılır
    if (hasAnyRole(roles, ['change_manager', 'cab'])) return '/degisiklikler';
    return hasAnyRole(roles, ['requester']) ? '/taleplerim' : '/yetkisiz';
  }
  if (hasAnyRole(roles, ['agent'])) return '/kayitlar';
  if (hasAnyRole(roles, ['approver'])) return '/onaylar';
  return '/yetkisiz';
}
