"""Olay yönetimi (Incident Management) API/servis şemaları."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from serviszeka.domain.enums import (
    DecisionOutcome,
    Impact,
    IncidentStatus,
    OnHoldReason,
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


class IncidentCreateRequest(IncidentCreate):
    """POST /incidents gövdesi.

    ``caller`` agent için zorunludur; agent olmayan kullanıcıda (self-servis ``requester``,
    ``requester`` + ``approver``) yok sayılır ve belirteçteki kullanıcı adı kullanılır. Agent
    olmayan kullanıcı ``impact`` belirleyemez (kurum geneli etkiyi son kullanıcı beyan edemez;
    triage/agent belirler).
    """

    caller: str | None = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]


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
    # ON_HOLD durumuna geçişte zorunlu
    on_hold_reason: OnHoldReason | None = None
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
    on_hold_reason: OnHoldReason | None = None
    sla_paused_at: UTCDateTime | None = None
    sla_paused_seconds: int = 0
    ai_confidence: float | None
    # Etkilenen yapılandırma öğeleri (CMDB CI id'leri); self-servis kullanıcıya boş döner
    ci_ids: list[str] = Field(default_factory=list)
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class IncidentCIsUpdate(BaseModel):
    """PUT /incidents/{id}/cis gövdesi: etkilenen CI kümesi (verilen küme eskisinin yerini alır)."""

    ci_ids: list[str] = Field(max_length=100)
    expected_version: int | None = Field(default=None, ge=1)


class AIDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    incident_id: str | None
    # Değişiklik kararları (ör. change.assess_risk) olay yerine değişikliğe bağlıdır
    change_id: str | None = None
    agent: str
    action: str
    input_summary: str
    output: dict[str, Any]
    rationale: str
    sources: list[str]
    model: str
    confidence: float
    outcome: DecisionOutcome
    incident_version: int | None = None
    approved_by: str | None
    approved_at: UTCDateTime | None
    created_at: UTCDateTime


class DecisionInboxItem(AIDecisionRead):
    """Onay kutusu satırı: karar + bağlı olayın numarası/başlığı."""

    incident_number: str | None = None
    incident_title: str | None = None
    incident_priority: Priority | None = None
    incident_status: IncidentStatus | None = None
    # Olayın güncel sürümü; karar sürümünden farklıysa onay 409 (yeniden triage) döner
    incident_current_version: int | None = None


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
