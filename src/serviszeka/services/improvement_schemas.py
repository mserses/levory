"""Sürekli iyileştirme (CSI) API/servis/araç şemaları (bkz. docs/faz4.md)."""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from serviszeka.domain.enums import ImprovementSource, ImprovementState
from serviszeka.domain.types import UTCDateTime

Id = Annotated[str, Field(min_length=1, max_length=36)]

KPIKey = Literal[
    "mttr",
    "mtta",
    "fcr_rate",
    "sla_response_compliance",
    "sla_resolve_compliance",
    "backlog",
    "reopen_rate",
    "automation_rate",
    "ai_triage_acceptance",
    "change_success_rate",
    "emergency_change_share",
    "problem_known_error_lead_time",
]

# KPI -> ölçülebilir kırılım boyutları (services/kpi.py kırılımlarıyla aynı)
MEASURABLE_DIMENSIONS: dict[str, frozenset[str]] = {
    "mttr": frozenset({"priority", "assignment_group", "category", "service"}),
    "mtta": frozenset({"priority", "assignment_group"}),
    "fcr_rate": frozenset({"priority"}),
    "sla_response_compliance": frozenset({"priority", "assignment_group", "category"}),
    "sla_resolve_compliance": frozenset({"priority", "assignment_group", "category"}),
    "backlog": frozenset({"priority", "assignment_group"}),
    "reopen_rate": frozenset({"assignment_group", "category"}),
    "automation_rate": frozenset({"item_key"}),
    "ai_triage_acceptance": frozenset({"category"}),
    "change_success_rate": frozenset({"change_type"}),
    "emergency_change_share": frozenset(),
    "problem_known_error_lead_time": frozenset(),
}

MAX_EVIDENCE_BYTES = 20_000
MAX_DESCRIPTION = 20_000


def _finite(value: float | None) -> float | None:
    if value is not None and not math.isfinite(value):
        raise ValueError("Değer sonlu bir sayı olmalı.")
    return value


Number = Annotated[float | None, AfterValidator(_finite)]


def _small_json(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is not None and len(json.dumps(value, default=str)) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"Kanıt en fazla {MAX_EVIDENCE_BYTES} bayt olabilir.")
    return value


Evidence = Annotated[dict[str, Any] | None, AfterValidator(_small_json)]


def check_kpi_scope(kpi_key: str | None, dimension: str | None, value: str | None) -> None:
    if (dimension is None) != (value is None):
        raise ValueError("kpi_dimension ve kpi_dimension_value birlikte verilmelidir.")
    if dimension is None:
        return
    if kpi_key is None:
        raise ValueError("Kırılım için kpi_key gerekir.")
    allowed = MEASURABLE_DIMENSIONS.get(kpi_key, frozenset())
    if dimension not in allowed:
        raise ValueError(
            f"{kpi_key} için ölçülebilir kırılım: {', '.join(sorted(allowed)) or 'yok'}."
        )


class ImprovementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    source: ImprovementSource = ImprovementSource.MANUAL
    kpi_key: KPIKey | None = Field(default=None, description="Bağlı KPI (services/kpi.py)")
    kpi_dimension: str | None = Field(default=None, min_length=1, max_length=50)
    kpi_dimension_value: str | None = Field(default=None, min_length=1, max_length=100)
    baseline_value: Number = Field(
        default=None, description="Boşsa KPI'dan son pencere üzerinden hesaplanır"
    )
    target_value: Number = None
    owner: str | None = Field(default=None, min_length=1, max_length=200)
    benefit_estimate: int = Field(default=3, ge=1, le=5, description="Fayda tahmini 1-5")
    effort: int = Field(default=3, ge=1, le=5, description="Efor 1-5")
    measure_days: int | None = Field(default=None, ge=1, le=366, description="Ölçüm penceresi")
    problem_id: Id | None = None
    change_id: Id | None = None
    evidence: Evidence = None
    signal_key: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _scope(self) -> Self:
        check_kpi_scope(self.kpi_key, self.kpi_dimension, self.kpi_dimension_value)
        return self


class ImprovementUpdate(BaseModel):
    """Kısmi güncelleme. KPI tanımı, taban ve hedef yalnızca onaydan önce (idea) değişir."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    owner: str | None = Field(default=None, max_length=200)
    benefit_estimate: int | None = Field(default=None, ge=1, le=5)
    effort: int | None = Field(default=None, ge=1, le=5)
    kpi_key: KPIKey | None = None
    kpi_dimension: str | None = Field(default=None, min_length=1, max_length=50)
    kpi_dimension_value: str | None = Field(default=None, min_length=1, max_length=100)
    baseline_value: Number = None
    target_value: Number = None
    measure_days: int | None = Field(default=None, ge=1, le=366)
    expected_version: int | None = Field(default=None, ge=1)


class ImprovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    title: str
    description: str
    source: ImprovementSource
    state: ImprovementState
    kpi_key: str | None
    kpi_dimension: str | None
    kpi_dimension_value: str | None
    baseline_value: float | None
    baseline_start: UTCDateTime | None
    baseline_end: UTCDateTime | None
    target_value: float | None
    measured_value: float | None
    measured_at: UTCDateTime | None
    target_met: bool | None
    measurement: dict[str, Any] | None
    measure_days: int
    owner: str | None
    benefit_estimate: int
    effort: int
    priority_score: float
    problem_id: str | None
    change_id: str | None
    evidence: dict[str, Any] | None
    signal_key: str | None
    detected_by: str
    created_by: str
    approved_by: str | None
    approved_at: UTCDateTime | None
    started_at: UTCDateTime | None
    implemented_at: UTCDateTime | None
    closed_by: str | None
    closed_at: UTCDateTime | None
    resolution: str | None
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ImprovementNote(BaseModel):
    """Durum adımı gövdesi (onay / red / başlatma / kapatma); red için not zorunlu."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=10_000)
    expected_version: int | None = Field(default=None, ge=1)


class ImplementedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: UTCDateTime | None = Field(default=None, description="Uygulama bitişi; boşsa şimdi")
    expected_version: int | None = Field(default=None, ge=1)


class SignalRead(BaseModel):
    key: str
    kind: str
    source: ImprovementSource
    title: str
    kpi_key: str | None
    kpi_dimension: str | None
    kpi_dimension_value: str | None
    baseline_value: float | None
    target_value: float | None
    benefit_estimate: int
    effort: int
    confidence: float
    problem_id: str | None
    change_id: str | None
    evidence: dict[str, Any]
    summary: str


class SignalsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_days: int = Field(default=30, ge=7, le=180)


class SignalsRead(BaseModel):
    window_days: int
    signals: list[SignalRead]


class ReviewRead(BaseModel):
    signals: int
    proposed: int
    measured: int
    pending_approval: int
