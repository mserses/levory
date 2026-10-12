"""Olay yönetimi (Incident Management) API/servis şemaları."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from serviszeka.domain.enums import (
    DecisionOutcome,
    Impact,
    IncidentStatus,
    Priority,
    TicketKind,
    Urgency,
)
from serviszeka.domain.types import UTCDateTime


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    caller: str = Field(min_length=1, max_length=200)
    impact: Impact | None = None
    urgency: Urgency | None = None
    service: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    kind: TicketKind | None = None


class IncidentUpdate(BaseModel):
    """Kısmi güncelleme: yalnızca gönderilen alanlar uygulanır (exclude_unset)."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    caller: str | None = Field(default=None, max_length=200)
    impact: Impact | None = None
    urgency: Urgency | None = None
    service: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=100)
    kind: TicketKind | None = None
    status: IncidentStatus | None = None
    assignee: str | None = Field(default=None, max_length=200)
    assignment_group: str | None = Field(default=None, max_length=100)
    resolution: str | None = None


class IncidentPatch(IncidentUpdate):
    """PATCH gövdesi: If-Match başlığı yerine sürüm gövdede de verilebilir."""

    expected_version: int | None = Field(default=None, ge=1)


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    kind: TicketKind
    title: str
    description: str
    caller: str
    category: str | None
    service: str | None
    impact: Impact
    urgency: Urgency
    priority: Priority
    status: IncidentStatus
    assignment_group: str | None
    assignee: str | None
    resolution: str | None
    response_due_at: UTCDateTime | None
    resolve_due_at: UTCDateTime | None
    responded_at: UTCDateTime | None
    resolved_at: UTCDateTime | None
    ai_confidence: float | None
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class AIDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    incident_id: str | None
    agent: str
    action: str
    input_summary: str
    output: dict[str, Any]
    rationale: str
    sources: list[str]
    model: str
    confidence: float
    outcome: DecisionOutcome
    approved_by: str | None
    approved_at: UTCDateTime | None
    created_at: UTCDateTime


class ApproveRequest(BaseModel):
    approver: str | None = Field(default=None, min_length=1, max_length=200)


class SLAAlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: str
    number: str
    priority: Priority
    metric: str
    state: str
    due_at: UTCDateTime
    elapsed_ratio: float
