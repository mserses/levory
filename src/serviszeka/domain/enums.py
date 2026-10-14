"""ITIL 4 alan sabitleri."""

from enum import StrEnum


class Impact(StrEnum):
    HIGH = "high"  # Birden çok hizmet / tüm kurum
    MEDIUM = "medium"  # Bir birim veya kritik kullanıcı grubu
    LOW = "low"  # Tek kullanıcı


class Urgency(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Priority(StrEnum):
    P1 = "P1"  # Kritik
    P2 = "P2"  # Yüksek
    P3 = "P3"  # Orta
    P4 = "P4"  # Düşük
    P5 = "P5"  # Planlama


class IncidentStatus(StrEnum):
    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class OnHoldReason(StrEnum):
    """Beklemeye alma nedeni; yalnızca arayan/tedarikçi beklemesi SLA saatini durdurur."""

    AWAITING_CALLER = "awaiting_caller"  # Arayandan bilgi/onay bekleniyor (saat durur)
    AWAITING_VENDOR = "awaiting_vendor"  # Tedarikçi bekleniyor (saat durur)
    AWAITING_CHANGE = "awaiting_change"  # Değişiklik bekleniyor (saat işler)
    INTERNAL = "internal"  # İç bekleme (saat işler)


class TicketKind(StrEnum):
    INCIDENT = "incident"
    SERVICE_REQUEST = "service_request"
    QUESTION = "question"


class ChangeType(StrEnum):
    STANDARD = "standard"  # Önceden onaylı, düşük riskli
    NORMAL = "normal"  # CAB değerlendirmesi gerekir
    EMERGENCY = "emergency"  # ECAB ile hızlı onay


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DecisionOutcome(StrEnum):
    """Bir yapay zekâ kararının politika motorundan geçtikten sonraki sonucu."""

    AUTO_APPLIED = "auto_applied"  # Yapay zekâ uyguladı
    SUGGESTED = "suggested"  # Gölge mod / öneri olarak bırakıldı
    NEEDS_APPROVAL = "needs_approval"  # İnsan onayı bekliyor
    ESCALATED = "escalated"  # Düşük güven: insana yönlendirildi
    REJECTED = "rejected"  # Politika reddetti


class ExternalSystem(StrEnum):
    SERVICENOW = "servicenow"
    JIRA_SM = "jira_sm"


class SyncDirection(StrEnum):
    INBOUND = "inbound"  # Harici sistem -> ServisZeka
    OUTBOUND = "outbound"  # ServisZeka -> harici sistem


# --- CMDB ---------------------------------------------------------------------


class CIClass(StrEnum):
    APPLICATION = "application"
    SERVICE = "service"  # İş hizmeti (etki analizinde "etkilenen iş hizmetleri")
    SERVER = "server"
    DATABASE = "database"
    NETWORK_DEVICE = "network_device"
    CLOUD_RESOURCE = "cloud_resource"


class CIEnvironment(StrEnum):
    PROD = "prod"
    TEST = "test"
    DEV = "dev"


class CIStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"


class CIRelationshipType(StrEnum):
    """Kaynak -> hedef ilişkisi.

    Etki yönü: depends_on / runs_on / connects_to'da hedef değişirse kaynak etkilenir;
    part_of'ta parça (kaynak) değişirse bütün (hedef) etkilenir.
    """

    DEPENDS_ON = "depends_on"
    RUNS_ON = "runs_on"
    CONNECTS_TO = "connects_to"
    PART_OF = "part_of"


# --- Değişiklik yönetimi ------------------------------------------------------


class ChangeState(StrEnum):
    DRAFT = "draft"
    ASSESS = "assess"  # Risk/etki değerlendirmesi
    AUTHORIZE = "authorize"  # CAB / ECAB onayı bekleniyor
    SCHEDULED = "scheduled"
    IMPLEMENT = "implement"
    REVIEW = "review"  # Uygulama sonrası gözden geçirme (PIR)
    CLOSED = "closed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"  # Uygulama başarısız -> review


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


# --- Faz 3: otomasyon (araçlar ve eylem istekleri) -----------------------------------


class RiskClass(StrEnum):
    """Araç risk sınıfı (ürün sahibiyle tasarlanan katalog).

    R0 okuma (yapay zekâ serbest); R1 düşük riskli yazma (yapay zekâ yalnızca politika izin
    verirse: gölge mod kapalı + güven >= eşik); R2 orta (her zaman insan onayı); R3 kritik
    (insan onayı + ikinci onaylayan ya da CAB onaylı değişiklik).
    """

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class ActionRequestState(StrEnum):
    PENDING = "pending"  # Onay bekliyor
    APPROVED = "approved"  # Son onay verildi, yürütülüyor (geçici)
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"  # Onaylandı ama yürütme başarısız (kısmi yazma geri alındı)
    EXPIRED = "expired"  # Süresi doldu; artık onaylanamaz


# --- Faz 3: hizmet kataloğu ve hizmet talepleri ------------------------------------------


class CatalogApprovalPolicy(StrEnum):
    """Katalog öğesinin iş onayı (risk onayından ayrıdır ve ondan önce gelir)."""

    NONE = "none"  # İş onayı gerekmez
    MANAGER = "manager"  # Talep edenin dizindeki yöneticisi onaylar
    GROUP = "group"  # Adı verilen dizin grubunun bir üyesi onaylar


class ServiceRequestState(StrEnum):
    SUBMITTED = "submitted"  # Alındı (iş onayı/karşılama henüz başlamadı)
    AWAITING_APPROVAL = "awaiting_approval"  # İş onayı (yönetici / grup) bekleniyor
    FULFILLING = "fulfilling"  # Karşılama adımları çalışıyor ya da bir eylem onayı bekleniyor
    FULFILLED = "fulfilled"
    REJECTED = "rejected"  # İş onayı ya da bir adımın eylem isteği reddedildi
    FAILED = "failed"  # Bir adım başarısız oldu / eylem isteğinin süresi doldu
    CANCELLED = "cancelled"


class BusinessApprovalState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# --- Faz 3: sorun yönetimi (ITIL 4 Problem Management) -----------------------------------


class ProblemState(StrEnum):
    """new -> investigating -> known_error -> resolved -> closed (bkz. services/problems.py)."""

    NEW = "new"
    INVESTIGATING = "investigating"  # Kök neden analizi sürüyor
    KNOWN_ERROR = "known_error"  # Kök neden ve/veya geçici çözüm belgelendi (KEDB)
    RESOLVED = "resolved"  # Kalıcı çözüm uygulandı
    CLOSED = "closed"


class RCAMethod(StrEnum):
    """Kök neden analizi yöntemi."""

    FIVE_WHY = "5_why"  # 5 Neden: her adım soru + yanıt
    TIMELINE = "timeline"  # Zaman çizelgesi: her adım zaman + olay


# --- Faz 4: kalıcı bildirim kuyruğu (notification_outbox) ---------------------------------


class NotificationState(StrEnum):
    PENDING = "pending"  # Gönderilecek (ilk deneme ya da kira süresi dolmuş)
    SENT = "sent"
    FAILED = "failed"  # Son deneme başarısız; next_attempt_at'te yeniden denenir
    DEAD = "dead"  # En fazla deneme aşıldı; artık gönderilmez (denetimde)


# --- Faz 4: izleme ve olay (event) yönetimi ------------------------------------------------


class EventStatus(StrEnum):
    """İzleme uyarısının durumu (Alertmanager `status`)."""

    FIRING = "firing"
    RESOLVED = "resolved"


class EventSeverity(StrEnum):
    """Normalleştirilmiş önem derecesi (kaynak etiketleri `normalize_severity` ile eşlenir)."""

    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    INFO = "info"


class EventGroupState(StrEnum):
    OPEN = "open"  # En az bir uyarı ateşleniyor
    RESOLVED = "resolved"  # Tüm uyarılar çözüldü
    MERGED = "merged"  # Kök nedeni açıklayan başka bir gruba katıldı (merged_into_id)


# --- Faz 4: sürekli iyileştirme (CSI kaydı) ------------------------------------------------


class ImprovementSource(StrEnum):
    """İyileştirme fikrinin kaynağı."""

    KPI = "kpi"  # KPI eğilimi / SLA tahmini
    PROBLEM = "problem"  # Sorun / bilinen hata
    PIR = "pir"  # Uygulama sonrası gözden geçirme (değişiklik)
    MANUAL = "manual"  # İnsan önerisi
    AI = "ai"  # Yapay zekâ performansı (triage kabulü, KB önerileri)


class ImprovementState(StrEnum):
    """idea -> approved -> in_progress -> measured -> closed; her açık durumdan rejected."""

    IDEA = "idea"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    MEASURED = "measured"
    CLOSED = "closed"
    REJECTED = "rejected"
