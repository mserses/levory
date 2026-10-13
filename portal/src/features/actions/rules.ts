/**
 * Eylem isteği karar kuralları (istemci tarafı; asıl denetim backend `tools/actions.py`'dedir):
 * yapay zekâ ve talep eden karar veremez; karar verebilen approver/admin, R3 istek değişikliğe
 * bağlıysa change_manager da; R1/R2 tek onay, R3 iki farklı onaylayan ya da CAB onaylı bağlı
 * değişiklik + tek onay; süresi dolan istek onaylanamaz.
 */
import type { ActionRequest } from '@/api/types';
import type { Role } from '@/auth/roles';
import { sameIdentity } from '@/lib/labels';

export type BlockReason = 'self' | 'already_decided' | 'role' | 'expired' | 'not_pending' | null;

export const BLOCK_MESSAGES: Record<Exclude<BlockReason, null>, string> = {
  self: 'Bu isteği siz başlattınız; görevler ayrılığı gereği kendi isteğinizi onaylayamaz ya da reddedemezsiniz. Başka bir onaylayıcı karar vermeli.',
  already_decided: 'Bu isteğe zaten karar verdiniz; kalan onay başka bir kişiden gelmeli.',
  role: 'Bu isteğe karar verme yetkiniz yok: onaylayıcılar karar verir; değişiklik yöneticileri yalnızca bir değişikliğe bağlı R3 isteklerde karar verebilir.',
  expired: 'İsteğin süresi doldu; artık onaylanamaz. Gerekirse eylem yeniden istenmelidir.',
  not_pending: 'Bu istek artık karar beklemiyor.',
};

export function isExpired(action: ActionRequest, now: Date = new Date()): boolean {
  if (action.state === 'expired') return true;
  return action.state === 'pending' && new Date(action.expires_at).getTime() <= now.getTime();
}

export function approvalCount(action: ActionRequest): number {
  return (action.approvals ?? []).filter((a) => a.decision === 'approve').length;
}

/** "R3: 1/2 onay" */
export function progressLabel(action: ActionRequest): string {
  return `${action.risk_class}: ${String(approvalCount(action))}/${String(action.required_approvals)} onay`;
}

interface Who {
  username: string;
  hasRole: (...roles: Role[]) => boolean;
}

export function blockReason(action: ActionRequest, who: Who, now: Date = new Date()): BlockReason {
  if (isExpired(action, now)) return 'expired';
  if (action.state !== 'pending') return 'not_pending';
  if (sameIdentity(who.username, action.requested_by)) return 'self';
  if ((action.approvals ?? []).some((a) => sameIdentity(a.approver, who.username))) {
    return 'already_decided';
  }
  const approver = who.hasRole('approver');
  const changeManagerOk =
    action.risk_class === 'R3' && action.change_id !== null && who.hasRole('change_manager');
  if (!approver && !changeManagerOk) return 'role';
  return null;
}

/** Bu onay eylemi yürütecek mi (son gereken onay) */
export function isFinalApproval(action: ActionRequest): boolean {
  return approvalCount(action) + 1 >= action.required_approvals;
}
