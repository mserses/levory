/** API enum değerlerinin Türkçe etiketleri. Ekranlar ham enum değeri göstermez. */
import type {
  DecisionOutcome,
  ExternalSystem,
  Impact,
  IncidentStatus,
  OnHoldReason,
  Priority,
  TicketKind,
  Urgency,
} from '@/api/types';

export const STATUS_LABELS: Record<IncidentStatus, string> = {
  new: 'Yeni',
  assigned: 'Atandı',
  in_progress: 'Çalışılıyor',
  on_hold: 'Beklemede',
  resolved: 'Çözüldü',
  closed: 'Kapatıldı',
  cancelled: 'İptal edildi',
};

export const PRIORITY_LABELS: Record<Priority, string> = {
  P1: 'P1 · Kritik',
  P2: 'P2 · Yüksek',
  P3: 'P3 · Orta',
  P4: 'P4 · Düşük',
  P5: 'P5 · Planlama',
};

export const IMPACT_LABELS: Record<Impact, string> = {
  high: 'Yüksek (kurum geneli / çok sayıda kullanıcı)',
  medium: 'Orta (bir birim / kritik grup)',
  low: 'Düşük (tek kullanıcı)',
};

export const URGENCY_LABELS: Record<Urgency, string> = {
  high: 'Yüksek — işim tamamen durdu',
  medium: 'Orta — çalışabiliyorum ama zorlanıyorum',
  low: 'Düşük — acil değil',
};

export const KIND_LABELS: Record<TicketKind, string> = {
  incident: 'Arıza / sorun',
  service_request: 'Hizmet talebi',
  question: 'Soru',
};

export const ON_HOLD_REASON_LABELS: Record<OnHoldReason, string> = {
  awaiting_caller: 'Kullanıcıdan yanıt bekleniyor (SLA durur)',
  awaiting_vendor: 'Tedarikçi bekleniyor (SLA durur)',
  awaiting_change: 'Değişiklik bekleniyor (SLA işler)',
  internal: 'İç bekleme (SLA işler)',
};

/** Yalnızca bu bekleme nedenleri SLA saatini durdurur (backend kuralı). */
export const SLA_PAUSING_REASONS: readonly OnHoldReason[] = ['awaiting_caller', 'awaiting_vendor'];

export const OUTCOME_LABELS: Record<DecisionOutcome, string> = {
  auto_applied: 'Otomatik uygulandı',
  suggested: 'Öneri',
  needs_approval: 'Onay bekliyor',
  escalated: 'İnsana yönlendirildi',
  rejected: 'Reddedildi',
};

export const OUTCOME_DESCRIPTIONS: Record<DecisionOutcome, string> = {
  auto_applied: 'Yapay zekâ bu değişikliği politika izin verdiği için uyguladı.',
  suggested: 'Yapay zekâ öneride bulundu; bir insan onaylarsa uygulanır.',
  needs_approval: 'Riskli değişiklik (ör. P1/P2 yükseltme/düşürme): insan onayı gerekli.',
  escalated: 'Güven düşük ya da durum belirsiz; kayıt insana yönlendirildi.',
  rejected: 'Politika bu eylemi reddetti; hiçbir şey uygulanmadı.',
};

/** Dış ITSM sistemleri (entegrasyonlar ve senkron çakışmaları ortak kullanır) */
export const SYSTEM_LABELS: Record<ExternalSystem, string> = {
  servicenow: 'ServiceNow',
  jira_sm: 'Jira Service Management',
};

export function systemLabel(system: string): string {
  return (SYSTEM_LABELS as Record<string, string>)[system] ?? system;
}

export const ROLE_LABELS: Record<string, string> = {
  requester: 'Son kullanıcı',
  agent: 'Destek uzmanı',
  approver: 'Onaylayıcı',
  admin: 'Yönetici',
};

export function label<K extends string>(
  map: Record<K, string>,
  value: K | null | undefined,
  fallback = '—',
): string {
  return value ? map[value] : fallback;
}
