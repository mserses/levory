"""ITSM entegrasyonlarının ortak tipleri: normalize kayıt, bağlayıcı protokolü, eşleme tabloları."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from serviszeka.domain.enums import ExternalSystem, Impact, IncidentStatus, Priority, Urgency


class ConnectorError(Exception):
    """Harici sistem çağrısı başarısız oldu (HTTP hatası, eksik geçiş vb.)."""


@dataclass
class RemoteTicket:
    """Harici sistemdeki kaydın ServisZeka enum'larına normalize edilmiş hali."""

    external_id: str  # ServiceNow sys_id / Jira issue key
    number: str | None  # INC0010001 / SD-12
    title: str
    description: str = ""
    status: IncidentStatus | None = None
    priority: Priority | None = None
    impact: Impact | None = None
    urgency: Urgency | None = None
    assignee: str | None = None
    updated_at: str | None = None  # Uzak sistemin kendi zaman damgası (olduğu gibi)
    raw: dict[str, Any] = field(default_factory=dict)


# Normalize alan sözlüğü anahtarları (create/update için):
# title, description, status, priority, impact, urgency, assignee, resolution
TICKET_FIELDS = (
    "title",
    "description",
    "status",
    "priority",
    "impact",
    "urgency",
    "assignee",
    "resolution",
)


@runtime_checkable
class ITSMConnector(Protocol):
    system: ExternalSystem

    async def get(self, external_id: str) -> RemoteTicket: ...

    async def list_updated_since(self, since: str | None) -> list[RemoteTicket]: ...

    async def create(self, fields: dict[str, Any]) -> RemoteTicket: ...

    async def update(self, external_id: str, fields: dict[str, Any]) -> RemoteTicket: ...


def incident_to_fields(incident: Any) -> dict[str, Any]:
    """ORM Incident -> bağlayıcıların beklediği normalize alan sözlüğü."""
    return {
        "title": incident.title,
        "description": incident.description or "",
        "status": incident.status,
        "priority": incident.priority,
        "impact": incident.impact,
        "urgency": incident.urgency,
        "assignee": incident.assignee,
        "resolution": incident.resolution,
    }


# --- ServiceNow eşlemeleri ---------------------------------------------------

SERVICENOW_STATE_TO_LOCAL: dict[str, IncidentStatus] = {
    "1": IncidentStatus.NEW,
    "2": IncidentStatus.IN_PROGRESS,
    "3": IncidentStatus.ON_HOLD,
    "6": IncidentStatus.RESOLVED,
    "7": IncidentStatus.CLOSED,
    "8": IncidentStatus.CANCELLED,
}
LOCAL_TO_SERVICENOW_STATE: dict[IncidentStatus, str] = {
    IncidentStatus.NEW: "1",
    IncidentStatus.ASSIGNED: "2",  # ServiceNow'da ayrı "atandı" durumu yok
    IncidentStatus.IN_PROGRESS: "2",
    IncidentStatus.ON_HOLD: "3",
    IncidentStatus.RESOLVED: "6",
    IncidentStatus.CLOSED: "7",
    IncidentStatus.CANCELLED: "8",
}

SERVICENOW_IMPACT_TO_LOCAL: dict[str, Impact] = {"1": Impact.HIGH, "2": Impact.MEDIUM, "3": Impact.LOW}
LOCAL_TO_SERVICENOW_IMPACT: dict[Impact, str] = {v: k for k, v in SERVICENOW_IMPACT_TO_LOCAL.items()}
SERVICENOW_URGENCY_TO_LOCAL: dict[str, Urgency] = {
    "1": Urgency.HIGH,
    "2": Urgency.MEDIUM,
    "3": Urgency.LOW,
}
LOCAL_TO_SERVICENOW_URGENCY: dict[Urgency, str] = {
    v: k for k, v in SERVICENOW_URGENCY_TO_LOCAL.items()
}
SERVICENOW_PRIORITY_TO_LOCAL: dict[str, Priority] = {
    "1": Priority.P1,
    "2": Priority.P2,
    "3": Priority.P3,
    "4": Priority.P4,
    "5": Priority.P5,
}
LOCAL_TO_SERVICENOW_PRIORITY: dict[Priority, str] = {
    v: k for k, v in SERVICENOW_PRIORITY_TO_LOCAL.items()
}

# --- Jira Service Management eşlemeleri --------------------------------------

# Uzak durum adı (küçük harf) -> yerel durum
JIRA_STATUS_TO_LOCAL: dict[str, IncidentStatus] = {
    "open": IncidentStatus.NEW,
    "to do": IncidentStatus.NEW,
    "waiting for support": IncidentStatus.NEW,
    "in progress": IncidentStatus.IN_PROGRESS,
    "work in progress": IncidentStatus.IN_PROGRESS,
    "pending": IncidentStatus.ON_HOLD,
    "waiting for customer": IncidentStatus.ON_HOLD,
    "escalated": IncidentStatus.IN_PROGRESS,
    "resolved": IncidentStatus.RESOLVED,
    "done": IncidentStatus.RESOLVED,
    "closed": IncidentStatus.CLOSED,
    "canceled": IncidentStatus.CANCELLED,
    "cancelled": IncidentStatus.CANCELLED,
    "declined": IncidentStatus.CANCELLED,
}
# Bilinmeyen durum adlarında Jira statusCategory anahtarına düşülür.
JIRA_STATUS_CATEGORY_TO_LOCAL: dict[str, IncidentStatus] = {
    "new": IncidentStatus.NEW,
    "indeterminate": IncidentStatus.IN_PROGRESS,
    "done": IncidentStatus.RESOLVED,
}
# Yerel durum -> geçiş hedefi olarak kabul edilen Jira durum adları (öncelik sırasıyla)
LOCAL_TO_JIRA_STATUS: dict[IncidentStatus, tuple[str, ...]] = {
    IncidentStatus.NEW: ("Open", "Waiting for support", "To Do"),
    IncidentStatus.ASSIGNED: ("In Progress", "Work in progress"),
    IncidentStatus.IN_PROGRESS: ("In Progress", "Work in progress"),
    IncidentStatus.ON_HOLD: ("Pending", "Waiting for customer"),
    IncidentStatus.RESOLVED: ("Resolved", "Done"),
    IncidentStatus.CLOSED: ("Closed", "Done"),
    IncidentStatus.CANCELLED: ("Canceled", "Cancelled", "Declined"),
}

JIRA_PRIORITY_TO_LOCAL: dict[str, Priority] = {
    "highest": Priority.P1,
    "high": Priority.P2,
    "medium": Priority.P3,
    "low": Priority.P4,
    "lowest": Priority.P5,
}
LOCAL_TO_JIRA_PRIORITY: dict[Priority, str] = {
    Priority.P1: "Highest",
    Priority.P2: "High",
    Priority.P3: "Medium",
    Priority.P4: "Low",
    Priority.P5: "Lowest",
}
