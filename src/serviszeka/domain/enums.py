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
