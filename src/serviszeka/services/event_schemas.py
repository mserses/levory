"""İzleme ve olay yönetimi şemaları: gelen webhook gövdeleri ve okuma modelleri.

Gelen gövdeler:

- **Prometheus Alertmanager webhook v4** (`POST /events/alertmanager`): `version` "4" olmalı;
  `alerts[]` öğelerinden `status`, `labels`, `annotations`, `startsAt`, `endsAt`, `fingerprint`
  okunur (diğer alanlar yok sayılır).
- **Genel JSON** (`POST /events/generic`): `GenericPayload` — `{"events": [{"source": "zabbix",
  "fingerprint": "...", "status": "firing", "severity": "critical", "title": "...",
  "labels": {...}, "annotations": {...}, "starts_at": "...", "ends_at": null}]}`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from serviszeka.domain.enums import EventGroupState, EventSeverity, EventStatus
from serviszeka.domain.types import UTCDateTime

MAX_ALERTS = 500
MAX_LABELS = 64

LabelMap = Annotated[dict[str, Any], Field(max_length=MAX_LABELS)]


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"]
    labels: LabelMap = Field(default_factory=dict)
    annotations: LabelMap = Field(default_factory=dict)
    startsAt: str = Field(max_length=64)
    endsAt: str | None = Field(default=None, max_length=64)
    fingerprint: str | None = Field(default=None, max_length=200)


class AlertmanagerPayload(BaseModel):
    """Alertmanager webhook gövdesi (sürüm 4)."""

    model_config = ConfigDict(extra="ignore")

    version: Literal["4"]
    status: Literal["firing", "resolved"] | None = None
    receiver: str = Field(default="", max_length=200)
    groupKey: str = Field(default="", max_length=2000)
    alerts: list[AlertmanagerAlert] = Field(min_length=1, max_length=MAX_ALERTS)


Source = Annotated[
    str, StringConstraints(min_length=1, max_length=50, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
]


class GenericEvent(BaseModel):
    """Genel izleme olayı. `fingerprint` boşsa kaynak + etiketlerden üretilir."""

    model_config = ConfigDict(extra="forbid")

    source: Source = "generic"
    fingerprint: str | None = Field(default=None, min_length=1, max_length=200)
    status: EventStatus = EventStatus.FIRING
    severity: str = Field(default="warning", max_length=30)
    title: str | None = Field(default=None, max_length=300)
    labels: LabelMap = Field(default_factory=dict)
    annotations: LabelMap = Field(default_factory=dict)
    starts_at: UTCDateTime | None = None
    ends_at: UTCDateTime | None = None


class GenericPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[GenericEvent] = Field(min_length=1, max_length=MAX_ALERTS)


# --- okuma modelleri ---------------------------------------------------------------------------


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    fingerprint: str
    status: EventStatus
    severity: EventSeverity
    title: str
    labels: dict[str, Any]
    annotations: dict[str, Any]
    starts_at: UTCDateTime
    ends_at: UTCDateTime | None
    ci_id: str | None
    ci_rule: str | None
    group_id: str | None
    suppressed: str | None
    received_count: int
    last_received_at: UTCDateTime
    changed_at: UTCDateTime


class EventGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    state: EventGroupState
    title: str
    root_ci_id: str | None
    fingerprint: str | None
    severity: EventSeverity
    event_count: int
    first_event_at: UTCDateTime
    last_event_at: UTCDateTime
    last_change_at: UTCDateTime
    resolved_at: UTCDateTime | None
    flapping: bool
    incident_id: str | None
    noted_at: UTCDateTime | None
    merged_into_id: str | None
    acknowledged_by: str | None
    acknowledged_at: UTCDateTime | None
    silenced_until: UTCDateTime | None
    silenced_by: str | None
    version: int


class CorrelatedMember(BaseModel):
    event_id: str
    title: str
    status: EventStatus
    severity: EventSeverity
    suppressed: str | None
    ci_id: str | None
    ci_name: str | None
    # Kök CI'dan bu uyarının CI'ına bağımlılık yolu (CI adları); kök CI'ın kendisi [kök]
    path: list[str]


class CorrelationRead(BaseModel):
    group: EventGroupRead
    root_ci_name: str | None
    members: list[CorrelatedMember]
    actionable: bool
    proposed_priority: str | None
    pending_action_request_id: str | None


class WebhookResult(BaseModel):
    duplicate: bool = False
    received: int = 0
    created: int = 0
    updated: int = 0
    ignored: int = 0
    group_ids: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class EventListRead(BaseModel):
    items: list[EventRead]


class EventGroupListRead(BaseModel):
    items: list[EventGroupRead]


class EventGroupDetailRead(CorrelationRead):
    events: list[EventRead]


__all__ = [
    "MAX_ALERTS",
    "AlertmanagerPayload",
    "CorrelationRead",
    "EventGroupDetailRead",
    "EventGroupListRead",
    "EventGroupRead",
    "EventListRead",
    "EventRead",
    "GenericPayload",
    "WebhookResult",
]
