/** API enum değerlerinin Türkçe etiketleri. Ekranlar ham enum değeri göstermez. */
import type {
  ActionRequestState,
  ApprovalDecision,
  BusinessApprovalState,
  CatalogApprovalPolicy,
  ChangeState,
  ChangeType,
  CIClass,
  CIEnvironment,
  CIRelationshipType,
  CIStatus,
  DecisionOutcome,
  EventGroupState,
  EventSeverity,
  EventStatus,
  ExternalSystem,
  Impact,
  ImprovementSource,
  ImprovementState,
  IncidentStatus,
  OnHoldReason,
  Priority,
  ProblemState,
  RCAMethod,
  RiskClass,
  RiskLevel,
  ServiceRequestState,
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
  change_manager: 'Değişiklik yöneticisi',
  cab: 'CAB üyesi',
  admin: 'Yönetici',
};

export function label<K extends string>(
  map: Record<K, string>,
  value: K | null | undefined,
  fallback = '—',
): string {
  return value ? map[value] : fallback;
}

// --- Faz 2: değişiklik yönetimi + CMDB -------------------------------------------------------

export const CHANGE_STATE_LABELS: Record<ChangeState, string> = {
  draft: 'Taslak',
  assess: 'Değerlendirme',
  authorize: 'Yetkilendirme (CAB)',
  scheduled: 'Zamanlandı',
  implement: 'Uygulanıyor',
  review: 'Gözden geçirme',
  closed: 'Kapatıldı',
  rejected: 'Reddedildi',
  cancelled: 'İptal edildi',
  failed: 'Başarısız',
};

export const CHANGE_TYPE_LABELS: Record<ChangeType, string> = {
  standard: 'Standart',
  normal: 'Normal',
  emergency: 'Acil',
};

export const CHANGE_TYPE_DESCRIPTIONS: Record<ChangeType, string> = {
  standard: 'Önceden onaylı, düşük riskli katalog değişikliği; CAB gerekmez.',
  normal: 'Risk değerlendirmesi ve CAB onayı gerekir.',
  emergency: 'Acil durum; ECAB (değişiklik yöneticisi) onayı ile hızlı yol.',
};

export const RISK_LEVEL_LABELS: Record<RiskLevel, string> = {
  low: 'Düşük',
  medium: 'Orta',
  high: 'Yüksek',
  critical: 'Kritik',
};

export const APPROVAL_DECISION_LABELS: Record<ApprovalDecision, string> = {
  approve: 'Onayladı',
  reject: 'Reddetti',
};

export const CI_CLASS_LABELS: Record<CIClass, string> = {
  application: 'Uygulama',
  service: 'İş hizmeti',
  server: 'Sunucu',
  database: 'Veritabanı',
  network_device: 'Ağ cihazı',
  cloud_resource: 'Bulut kaynağı',
};

export const CI_ENVIRONMENT_LABELS: Record<CIEnvironment, string> = {
  prod: 'Üretim',
  test: 'Test',
  dev: 'Geliştirme',
};

export const CI_STATUS_LABELS: Record<CIStatus, string> = {
  planned: 'Planlandı',
  active: 'Etkin',
  maintenance: 'Bakımda',
  retired: 'Kullanım dışı',
};

/** "Kaynak … hedef" okunuşu: kaynak CI hedef CI'a bağlıdır / üzerinde çalışır … */
export const CI_RELATIONSHIP_LABELS: Record<CIRelationshipType, string> = {
  depends_on: 'bağımlıdır',
  runs_on: 'üzerinde çalışır',
  connects_to: 'bağlanır',
  part_of: 'parçasıdır',
};

/** Kritiklik 1–4 (1 = en kritik) */
export const CRITICALITY_LABELS: Record<number, string> = {
  1: 'K1 · Çok kritik',
  2: 'K2 · Kritik',
  3: 'K3 · Orta',
  4: 'K4 · Düşük',
};

export function criticalityLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return CRITICALITY_LABELS[value] ?? `K${value}`;
}

// --- Faz 3: hizmet talepleri, eylem istekleri, sorun yönetimi --------------------------------

export const SERVICE_REQUEST_STATE_LABELS: Record<ServiceRequestState, string> = {
  submitted: 'Gönderildi',
  awaiting_approval: 'İş onayı bekliyor',
  fulfilling: 'Karşılanıyor',
  fulfilled: 'Karşılandı',
  rejected: 'Reddedildi',
  failed: 'Başarısız',
  cancelled: 'İptal edildi',
};

export const BUSINESS_APPROVAL_LABELS: Record<BusinessApprovalState, string> = {
  not_required: 'Gerekmiyor',
  pending: 'Bekliyor',
  approved: 'Onaylandı',
  rejected: 'Reddedildi',
};

/** Karşılama adımı durumu (talep `step_results[].status`); bilinmeyen değer "Bilinmiyor" */
export const STEP_STATUS_LABELS: Record<string, string> = {
  executed: 'Tamamlandı',
  pending_approval: 'Onay bekliyor',
  waiting_manual: 'El ile yapılacak',
  skipped: 'Atlandı',
  failed: 'Başarısız',
  queued: 'Sırada',
};

export const ACTION_STATE_LABELS: Record<ActionRequestState, string> = {
  pending: 'Onay bekliyor',
  approved: 'Onaylandı',
  rejected: 'Reddedildi',
  executed: 'Yürütüldü',
  failed: 'Başarısız',
  expired: 'Süresi doldu',
};

export const RISK_CLASS_LABELS: Record<RiskClass, string> = {
  R0: 'R0 · Okuma',
  R1: 'R1 · Düşük risk',
  R2: 'R2 · Orta risk',
  R3: 'R3 · Kritik',
};

export const RISK_CLASS_DESCRIPTIONS: Record<RiskClass, string> = {
  R0: 'Salt okuma; onay gerekmez.',
  R1: 'Düşük riskli yazma; politika izin vermediğinde insan onayı ister.',
  R2: 'Orta risk; her zaman bir insan onayı gerekir.',
  R3: 'Kritik; iki farklı onaylayan ya da CAB onaylı bağlı değişiklik + bir onay gerekir.',
};

export const PROBLEM_STATE_LABELS: Record<ProblemState, string> = {
  new: 'Yeni',
  investigating: 'İnceleniyor',
  known_error: 'Bilinen hata',
  resolved: 'Çözüldü',
  closed: 'Kapandı',
};

export const RCA_METHOD_LABELS: Record<RCAMethod, string> = {
  '5_why': '5 Neden',
  timeline: 'Zaman çizelgesi',
};

export const CATALOG_APPROVAL_LABELS: Record<CatalogApprovalPolicy, string> = {
  none: 'İş onayı gerekmez',
  manager: 'Yönetici onayı gerekir',
  group: 'Grup onayı gerekir',
};

/** Aktör gösterimi: `ai:<ajan>` → "Otomasyon (ai:<ajan>)", `system:` → "Sistem", insan → kullanıcı adı */
export function actorLabel(actor: string | null | undefined): string {
  if (!actor) return '—';
  if (actor.startsWith('ai:')) return `Otomasyon (${actor})`;
  if (actor.startsWith('system:')) return 'Sistem';
  return actor;
}

/** Backend `changes.same_identity` ile aynı: büyük/küçük harf ve kenar boşluğu duyarsız */
export function sameIdentity(a: string | null | undefined, b: string | null | undefined): boolean {
  if (a === null || a === undefined || b === null || b === undefined) return false;
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

// Faz 4: izleme / olay yönetimi, tahmine dayalı SLA, sürekli iyileştirme

export const EVENT_SEVERITY_LABELS: Record<EventSeverity, string> = {
  critical: 'Kritik',
  high: 'Yüksek',
  warning: 'Uyarı',
  info: 'Bilgi',
};

export const EVENT_STATUS_LABELS: Record<EventStatus, string> = {
  firing: 'Etkin',
  resolved: 'Çözüldü',
};

export const EVENT_GROUP_STATE_LABELS: Record<EventGroupState, string> = {
  open: 'Açık',
  resolved: 'Çözüldü',
  merged: 'Birleştirildi',
};

/** Uyarının bastırılma nedeni (`EventRead.suppressed`) */
export const EVENT_SUPPRESSED_LABELS: Record<string, string> = {
  flapping: 'Çırpınıyor (bastırıldı)',
  silenced: 'Susturuldu',
};

/** Tahmin dayanağı (`BreachForecastRead.basis`) */
export const FORECAST_BASIS_LABELS: Record<string, string> = {
  priority_group: 'Aynı öncelik ve grup',
  priority: 'Yalnızca aynı öncelik',
  none: 'Örneklem yetersiz',
  breached: 'Hedef süre geçti',
};

export const IMPROVEMENT_STATE_LABELS: Record<ImprovementState, string> = {
  idea: 'Fikir',
  approved: 'Onaylandı',
  in_progress: 'Uygulanıyor',
  measured: 'Ölçüldü',
  closed: 'Kapandı',
  rejected: 'Reddedildi',
};

export const IMPROVEMENT_SOURCE_LABELS: Record<ImprovementSource, string> = {
  kpi: 'KPI eğilimi',
  problem: 'Sorun / bilinen hata',
  pir: 'Değişiklik sonrası gözden geçirme',
  manual: 'İnsan önerisi',
  ai: 'Yapay zekâ performansı',
};

/** KPI kırılım boyutları (`services/kpi.py` breakdown anahtarları) */
export const KPI_DIMENSION_LABELS: Record<string, string> = {
  priority: 'Öncelik',
  assignment_group: 'Çözüm grubu',
  category: 'Kategori',
  service: 'Hizmet',
  item_key: 'Katalog öğesi',
  change_type: 'Değişiklik türü',
};
