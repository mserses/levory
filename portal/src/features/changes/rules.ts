/**
 * Değişiklik durum makinesi ve CAB kuralları — backend `services/changes.py`'nin istemci
 * kopyası. Yalnızca hangi düğmenin gösterileceğini belirler; asıl denetim sunucudadır.
 */
import type { Change, ChangeApproval, ChangeState } from '@/api/types';
import { hasAnyRole } from '@/auth/roles';
import { CHANGE_STATE_LABELS } from '@/lib/labels';

export interface ChangeActor {
  username: string;
  roles: readonly string[];
}

const ALLOWED: Record<ChangeState, readonly ChangeState[]> = {
  draft: ['assess', 'cancelled'],
  assess: ['draft', 'authorize', 'scheduled', 'cancelled'],
  authorize: ['scheduled', 'rejected', 'cancelled'],
  scheduled: ['implement', 'cancelled'],
  implement: ['review', 'failed'],
  failed: ['review'],
  review: ['closed'],
  closed: [],
  rejected: [],
  cancelled: [],
};

export const TERMINAL_STATES: readonly ChangeState[] = ['closed', 'rejected', 'cancelled'];

const isAiActor = (username: string) => username.startsWith('ai:');

export const isChangeManager = (roles: readonly string[]) => hasAnyRole(roles, ['change_manager']);

/** Uygulayıcı: assignee, yoksa talep eden */
export const implementerOf = (change: Change) => change.assignee ?? change.requested_by;

function pathAllowed(change: Change, from: ChangeState, to: ChangeState): boolean {
  const standard = change.change_type === 'standard';
  if (to === 'authorize' && standard) return false;
  if (from === 'assess' && to === 'scheduled' && !standard) return false;
  return true;
}

function roleAllowed(change: Change, to: ChangeState, actor: ChangeActor): boolean {
  const manager = isChangeManager(actor.roles);
  const from = change.state;
  if (
    to === 'assess' ||
    to === 'draft' ||
    (to === 'cancelled' && ['draft', 'assess'].includes(from))
  )
    return manager || actor.username === change.requested_by;
  if (to === 'scheduled' && change.change_type === 'standard')
    return manager || actor.username === change.requested_by;
  if (to === 'implement' || to === 'failed' || to === 'review')
    return manager || actor.username === implementerOf(change);
  return manager;
}

/** Kullanıcının bu değişiklikte yapabileceği geçişler (sunucu ön koşulları ayrıca denetler) */
export function allowedTransitions(change: Change, actor: ChangeActor): ChangeState[] {
  if (isAiActor(actor.username)) return [];
  return ALLOWED[change.state].filter(
    (to) => pathAllowed(change, change.state, to) && roleAllowed(change, to, actor),
  );
}

/** Geçiş düğmesi metinleri */
export function transitionLabel(change: Change, to: ChangeState): string {
  switch (to) {
    case 'assess':
      return 'Değerlendirmeye gönder';
    case 'draft':
      return 'Taslağa geri al';
    case 'authorize':
      return change.change_type === 'emergency' ? 'ECAB onayına gönder' : 'CAB onayına gönder';
    case 'scheduled':
      return 'Zamanla';
    case 'implement':
      return 'Uygulamayı başlat';
    case 'review':
      return change.state === 'failed' ? 'Gözden geçirmeye al' : 'Başarıyla tamamlandı';
    case 'failed':
      return 'Başarısız oldu';
    case 'closed':
      return 'Kapat';
    case 'rejected':
      return 'Reddet';
    case 'cancelled':
      return 'İptal et';
    default:
      return CHANGE_STATE_LABELS[to];
  }
}

/** Onay isteyen (geri alınamaz) geçişler */
export const DESTRUCTIVE_TRANSITIONS: readonly ChangeState[] = ['cancelled', 'rejected', 'failed'];

/** Oy veremiyorsa kullanıcıya gösterilecek gerekçe; verebiliyorsa null */
export function voteBlockReason(
  change: Change,
  approvals: readonly ChangeApproval[],
  actor: ChangeActor,
): string | null {
  if (isAiActor(actor.username)) return 'Yapay zekâ aktörü onay veremez.';
  if (!hasAnyRole(actor.roles, ['cab', 'change_manager']))
    return 'Oy vermek için CAB üyesi ya da değişiklik yöneticisi olmalısınız.';
  if (actor.username === change.requested_by)
    return 'Talep eden olduğunuz değişiklik hakkında karar veremezsiniz (görevler ayrılığı).';
  if (change.assignee && actor.username === change.assignee)
    return 'Uygulayıcısı olduğunuz değişiklik hakkında karar veremezsiniz (görevler ayrılığı).';
  if (change.state !== 'authorize') return 'Oy yalnızca yetkilendirme aşamasında verilebilir.';
  if (approvals.some((a) => a.approver === actor.username))
    return 'Bu değişiklik için kararınızı verdiniz.';
  return null;
}

export function isAwaitingMyVote(
  change: Change,
  approvals: readonly ChangeApproval[],
  actor: ChangeActor,
): boolean {
  return voteBlockReason(change, approvals, actor) === null;
}

export type StepStatus = 'done' | 'current' | 'upcoming';

export interface Step {
  state: ChangeState;
  label: string;
  status: StepStatus;
}

/** Aşama göstergesi: türe göre yol (standart CAB'ı atlar), başarısız uygulama yolda görünür */
export function stepsFor(change: Change): Step[] {
  const failedPath = change.state === 'failed' || change.implementation_outcome === 'failed';
  const path: ChangeState[] = [
    'draft',
    'assess',
    ...(change.change_type === 'standard' ? [] : (['authorize'] as const)),
    'scheduled',
    'implement',
    ...(failedPath ? (['failed'] as const) : []),
    'review',
    'closed',
  ];
  const index = path.indexOf(change.state);
  return path.map((state, i) => ({
    state,
    label: CHANGE_STATE_LABELS[state],
    status: index < 0 ? 'upcoming' : i < index ? 'done' : i === index ? 'current' : 'upcoming',
  }));
}

/** Uygulama sonrası gözden geçirme (PIR) notu zorunlu mu (acil ya da başarısız)? */
export function pirRequired(change: Change): boolean {
  return change.change_type === 'emergency' || change.implementation_outcome === 'failed';
}

/** "Riski değerlendir": agent/change_manager (POST /assess) ve yalnızca taslak/değerlendirme */
export function canAssessRisk(change: Change, roles: readonly string[]): boolean {
  return (
    (change.state === 'draft' || change.state === 'assess') &&
    hasAnyRole(roles, ['agent', 'change_manager'])
  );
}
