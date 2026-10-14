import type { ChangeCreate, ChangeType } from '@/api/types';

export interface ChangeFormValues {
  title: string;
  description: string;
  changeType: ChangeType;
  templateId: string;
  implementationPlan: string;
  backoutPlan: string;
  testPlan: string;
  /** datetime-local değeri (yerel saat, "2026-10-20T22:00") */
  plannedStart: string;
  plannedEnd: string;
  assignmentGroup: string;
  assignee: string;
  ciIds: string[];
}

export type ChangeFormField = 'title' | 'templateId' | 'plannedStart' | 'plannedEnd' | 'assignee';

export type ChangeFormErrors = Partial<Record<ChangeFormField, string>>;

export const TITLE_MAX = 300;

export const EMPTY_CHANGE_FORM: ChangeFormValues = {
  title: '',
  description: '',
  changeType: 'normal',
  templateId: '',
  implementationPlan: '',
  backoutPlan: '',
  testPlan: '',
  plannedStart: '',
  plannedEnd: '',
  assignmentGroup: '',
  assignee: '',
  ciIds: [],
};

/** datetime-local (yerel) → Date; boş/geçersizse null */
export function parseLocalDateTime(value: string): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function validateChangeForm(v: ChangeFormValues): ChangeFormErrors {
  const errors: ChangeFormErrors = {};
  const title = v.title.trim();
  if (!title) errors.title = 'Başlık zorunludur.';
  else if (title.length > TITLE_MAX)
    errors.title = `Başlık en fazla ${TITLE_MAX} karakter olabilir.`;
  if (v.changeType === 'standard' && !v.templateId)
    errors.templateId = 'Standart değişiklik için bir şablon seçin.';
  const start = parseLocalDateTime(v.plannedStart);
  const end = parseLocalDateTime(v.plannedEnd);
  if (v.plannedStart && !start) errors.plannedStart = 'Geçerli bir tarih/saat girin.';
  if (v.plannedEnd && !end) errors.plannedEnd = 'Geçerli bir tarih/saat girin.';
  if (start && !v.plannedEnd) errors.plannedEnd = 'Bitişi de girin.';
  if (end && !v.plannedStart) errors.plannedStart = 'Başlangıcı da girin.';
  if (start && end && end <= start)
    errors.plannedEnd = 'Planlanan bitiş, başlangıçtan sonra olmalıdır.';
  if (v.assignee.includes(':')) errors.assignee = 'Kullanıcı adı ":" içeremez.';
  return errors;
}

/** Form → POST /changes gövdesi (boş alanlar gönderilmez; zamanlar UTC ISO) */
export function toChangeCreate(v: ChangeFormValues): ChangeCreate {
  const start = parseLocalDateTime(v.plannedStart);
  const end = parseLocalDateTime(v.plannedEnd);
  return {
    title: v.title.trim(),
    description: v.description.trim(),
    change_type: v.changeType,
    ...(v.changeType === 'standard' && v.templateId ? { template_id: v.templateId } : {}),
    implementation_plan: v.implementationPlan.trim(),
    backout_plan: v.backoutPlan.trim(),
    test_plan: v.testPlan.trim(),
    ...(start ? { planned_start: start.toISOString() } : {}),
    ...(end ? { planned_end: end.toISOString() } : {}),
    ...(v.assignmentGroup ? { assignment_group: v.assignmentGroup } : {}),
    ...(v.assignee.trim() ? { assignee: v.assignee.trim() } : {}),
    affected_ci_ids: v.ciIds,
  };
}
