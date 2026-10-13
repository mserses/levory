/**
 * Rota erişim haritası — gezinme menüsü ve rota korumaları BURADAN beslenir.
 * Yeni ekran eklerken önce buraya ekleyin; roller backend uç nokta rolleriyle uyumlu olmalı.
 */
import { hasAnyRole, isRequesterOnly, type Role } from '@/auth/roles';

export interface NavItem {
  path: string;
  label: string;
  roles: readonly Role[];
}

export const ROUTE_ROLES = {
  newRequest: ['requester', 'agent'], // POST /incidents
  myRequests: ['requester', 'agent', 'approver'], // GET /incidents?mine=true
  incidents: ['agent', 'approver'], // GET /incidents (tümü)
  approvals: ['approver'], // GET /incidents/decisions
  knowledge: ['agent', 'approver'], // GET /knowledge/*
  changes: ['agent', 'change_manager', 'cab'], // GET /changes, /changes/{id} (ChangeViewerUser)
  newChange: ['agent', 'change_manager'], // POST /changes (ChangeCreatorUser)
  cmdb: ['agent', 'change_manager', 'cab'], // GET /cmdb/* (CMDBReaderUser)
  audit: ['admin'], // GET /audit
  integrations: ['admin'], // /integrations/*
} as const satisfies Record<string, readonly Role[]>;

export const NAV_ITEMS: readonly NavItem[] = [
  { path: '/talep', label: 'Yeni talep', roles: ROUTE_ROLES.newRequest },
  { path: '/taleplerim', label: 'Taleplerim', roles: ROUTE_ROLES.myRequests },
  { path: '/kayitlar', label: 'Kayıtlar', roles: ROUTE_ROLES.incidents },
  { path: '/onaylar', label: 'Onaylar', roles: ROUTE_ROLES.approvals },
  { path: '/bilgi-bankasi', label: 'Bilgi bankası', roles: ROUTE_ROLES.knowledge },
  { path: '/degisiklikler', label: 'Değişiklikler', roles: ROUTE_ROLES.changes },
  { path: '/cmdb', label: 'CMDB', roles: ROUTE_ROLES.cmdb },
  { path: '/yonetim/denetim', label: 'Denetim', roles: ROUTE_ROLES.audit },
  { path: '/yonetim/entegrasyonlar', label: 'Entegrasyonlar', roles: ROUTE_ROLES.integrations },
];

export function navItemsFor(roles: readonly string[]): NavItem[] {
  return NAV_ITEMS.filter((item) => hasAnyRole(roles, item.roles));
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
