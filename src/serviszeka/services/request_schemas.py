"""Hizmet kataloğu ve hizmet talebi API/araç şemaları."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from serviszeka.domain.enums import (
    ActionRequestState,
    BusinessApprovalState,
    CatalogApprovalPolicy,
    RiskClass,
    ServiceRequestState,
)
from serviszeka.domain.types import UTCDateTime


class CatalogItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    name: str
    description: str
    category: str
    form_schema: dict[str, Any]
    approval_policy: CatalogApprovalPolicy
    approval_group: str | None
    fulfilment_plan: list[dict[str, Any]] = Field(
        description="Sıralı adımlar: {tool, title, args, when} ya da {manual: true, title}"
    )
    standard: bool
    active: bool
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class LinkedActionRead(BaseModel):
    """Talebe bağlı eylem isteğinin özeti (ham argümanlar yok)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    tool: str
    risk_class: RiskClass
    state: ActionRequestState
    required_approvals: int
    expires_at: UTCDateTime


class ServiceRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    catalog_item_id: str
    item_key: str
    item_name: str
    standard: bool
    requester: str
    opened_by: str
    form_values: dict[str, Any]
    state: ServiceRequestState
    approval_policy: CatalogApprovalPolicy
    approval_state: BusinessApprovalState
    approver: str | None = Field(description="Beklenen iş onaylayanı (yönetici UPN'si ya da grup)")
    approved_by: str | None
    approved_at: UTCDateTime | None
    approval_comment: str
    plan: list[dict[str, Any]]
    current_step: int
    attempt: int
    step_results: list[dict[str, Any]]
    waiting_action_id: str | None
    error: str | None
    human_approvals: int
    human_edits: int
    touchless: bool | None = Field(
        description="İnsansız çözüldü mü (karşılandı + sıfır insan onayı + sıfır insan düzenlemesi)"
    )
    touchless_reason: str | None
    touches: list[dict[str, Any]]
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime
    fulfilled_at: UTCDateTime | None
    closed_at: UTCDateTime | None
    actions: list[LinkedActionRead] = Field(default_factory=list)


class ServiceRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1, max_length=100, description="Katalog öğesi kimliği ya da anahtarı")
    form_values: dict[str, Any] = Field(default_factory=dict)
    requester: str | None = Field(
        default=None,
        max_length=200,
        description="Başkası adına talep (yalnızca agent/admin); verilmezse oturumdaki kullanıcı",
    )


class ServiceRequestUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_values: dict[str, Any]
    expected_version: int | None = Field(default=None, ge=1)


class RequestDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class RequestActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class TouchlessItemMetrics(BaseModel):
    item_key: str
    item_name: str
    standard: bool
    closed: int
    fulfilled: int
    touchless: int


class TouchlessMetricsRead(BaseModel):
    standard_closed: int
    standard_fulfilled: int
    standard_touchless: int
    standard_touchless_rate: float
    all_closed: int
    all_touchless: int
    gate_target: float
    meets_gate: bool
    by_item: list[TouchlessItemMetrics]
