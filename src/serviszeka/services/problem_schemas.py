"""Sorun yönetimi (Problem Management) API/servis/araç şemaları."""

from __future__ import annotations

from typing import Annotated, Any, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from serviszeka import taxonomy
from serviszeka.domain.enums import Priority, ProblemState, RCAMethod
from serviszeka.domain.types import UTCDateTime

Id = Annotated[str, Field(min_length=1, max_length=36)]
IdList = Annotated[list[Id], Field(max_length=200)]

# 5 Neden en fazla 10 adım, zaman çizelgesi en fazla 50 olay
MAX_FIVE_WHY_STEPS = 10
MAX_TIMELINE_STEPS = 50


def _taxonomy(field: str):
    def check(value: str | None) -> str | None:
        if value is not None and value not in taxonomy.allowed(field):
            raise ValueError(f"{field} sınıf listesinde yok: {value}")
        return value

    return AfterValidator(check)


Category = Annotated[str | None, Field(default=None, max_length=100), _taxonomy("category")]
AssignmentGroup = Annotated[
    str | None, Field(default=None, max_length=100), _taxonomy("assignment_group")
]


class RCAStep(BaseModel):
    """5 Neden adımı (soru + yanıt) ya da zaman çizelgesi olayı (zaman + olay)."""

    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=4000, description="Yanıt ya da olay")
    at: AwareDatetime | None = Field(default=None, description="Zaman çizelgesi olayı zamanı")


class RCA(BaseModel):
    """Kök neden analizi (yapılandırılmış)."""

    model_config = ConfigDict(extra="forbid")

    method: RCAMethod
    steps: list[RCAStep] = Field(min_length=1, max_length=MAX_TIMELINE_STEPS)
    root_cause: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.method == RCAMethod.FIVE_WHY:
            if len(self.steps) > MAX_FIVE_WHY_STEPS:
                raise ValueError(f"5 Neden en fazla {MAX_FIVE_WHY_STEPS} adım içerir.")
            if any(s.question is None for s in self.steps):
                raise ValueError("5 Neden adımlarında soru (question) zorunludur.")
        elif any(s.at is None for s in self.steps):
            raise ValueError("Zaman çizelgesi adımlarında zaman (at) zorunludur.")
        return self


class ProblemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20000)
    priority: Priority = Priority.P3
    category: Category = None
    assignment_group: AssignmentGroup = None
    workaround: str | None = Field(default=None, max_length=20000)
    incident_ids: IdList = Field(default_factory=list)
    ci_ids: IdList = Field(default_factory=list)


class ProblemUpdate(BaseModel):
    """Kısmi güncelleme (exclude_unset). Kök neden/RCA buradan değil, RCA uçlarından yazılır."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    priority: Priority | None = None
    category: Category = None
    assignment_group: AssignmentGroup = None
    workaround: str | None = Field(default=None, max_length=20000)
    expected_version: int | None = Field(default=None, ge=1)


class ProblemTransitionRequest(BaseModel):
    to_state: ProblemState
    # Kapanış/çözüm notu: çözülmemiş sorunu kapatırken (ör. yinelenen kayıt) zorunlu
    note: str | None = Field(default=None, min_length=1, max_length=10000)
    expected_version: int | None = Field(default=None, ge=1)


class IncidentLinkRequest(BaseModel):
    incident_ids: IdList = Field(min_length=1)


class CILinkRequest(BaseModel):
    ci_ids: IdList = Field(min_length=1)


class ChangeLinkRequest(BaseModel):
    change_ids: IdList = Field(min_length=1)


class RCARecordRequest(BaseModel):
    rca: RCA
    expected_version: int | None = Field(default=None, ge=1)


class ProblemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    title: str
    description: str
    state: ProblemState
    priority: Priority
    category: str | None
    assignment_group: str | None
    root_cause: str | None
    rca: dict[str, Any] | None
    rca_draft: dict[str, Any] | None
    workaround: str | None
    known_error_article_id: str | None
    incident_ids: list[str]
    affected_ci_ids: list[str]
    change_ids: list[str]
    detected_by: str
    created_by: str
    resolution: str | None
    resolved_at: UTCDateTime | None
    closed_at: UTCDateTime | None
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class TrendsRequest(BaseModel):
    window_days: int = Field(default=30, ge=1, le=365, description="Geriye dönük gün sayısı")


class TrendClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str  # ci_category / similar_title
    key: str
    title: str
    incident_ids: list[str]
    incident_numbers: list[str]
    ci_id: str | None
    category: str | None
    size: int
    similarity: float
    confidence: float
    priority: Priority


class TrendsRead(BaseModel):
    window_days: int
    clusters: list[TrendClusterRead]


class ProposeFixRequest(BaseModel):
    """Kalıcı çözüm için taslak RFC; boş alanlar sorundan doldurulur."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    implementation_plan: str = Field(default="", max_length=20000)
    backout_plan: str = Field(default="", max_length=20000)
    test_plan: str = Field(default="", max_length=20000)


class AgentResultRead(BaseModel):
    """Ajan araç çağrısının sonucu (`ToolResult.to_dict()` ile aynı alanlar)."""

    status: str
    tool: str
    risk_class: str
    action_request_id: str | None = None
    decision_id: str | None = None
    message: str = ""
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    replayed: bool = False


class AgentResultsRead(BaseModel):
    results: list[AgentResultRead]
