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
