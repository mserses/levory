/** Roller backend ile aynıdır (serviszeka.auth). admin tüm rolleri kapsar. */
export const ROLES = ['requester', 'agent', 'approver', 'admin'] as const;
export type Role = (typeof ROLES)[number];

export function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}

/** Kullanıcı verilen rollerden en az birine sahip mi? (admin her zaman evet; boş liste = herkes) */
export function hasAnyRole(userRoles: readonly string[], required: readonly Role[]): boolean {
  if (required.length === 0) return true;
  if (userRoles.includes('admin')) return true;
  return required.some((r) => userRoles.includes(r));
}

/** Yalnızca self-servis kullanıcı mı (backend `is_requester_only` ile aynı mantık)? */
export function isRequesterOnly(userRoles: readonly string[]): boolean {
  return !hasAnyRole(userRoles, ['agent', 'approver']);
}
