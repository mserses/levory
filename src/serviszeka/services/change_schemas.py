"""Değişiklik yönetimi (Change Enablement) API/servis şemaları."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from serviszeka.domain.enums import (
    ApprovalDecision,
    ChangeState,
    ChangeType,
    CIClass,
    CIEnvironment,
    RiskLevel,
)
from serviszeka.domain.types import UTCDateTime

# Girdi zamanları saat dilimli olmak zorunda (saat dilimsiz -> 422); UTC'ye çevrilerek saklanır
AwareUTC = Annotated[AwareDatetime, AfterValidator(lambda v: v.astimezone(UTC))]


def _strip_user(value: object) -> object:
    # Uygulayıcı serbest metin: kenar boşluğu atılır, boş -> None (öz onay denetimi için)
    if isinstance(value, str):
        return value.strip() or None
    return value


Assignee = Annotated[str | None, BeforeValidator(_strip_user), Field(max_length=200)]

_IdList = Annotated[list[Annotated[str, Field(min_length=1, max_length=36)]], Field(max_length=200)]


def _check_window(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end <= start:
        raise ValueError("Planlanan bitiş, başlangıçtan sonra olmalıdır.")


class ChangeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    change_type: ChangeType
    # Standart değişiklikte zorunlu: önceden onaylı katalog öğesi
    template_id: str | None = Field(default=None, max_length=36)
    implementation_plan: str = ""
    backout_plan: str = ""
    test_plan: str = ""
    planned_start: AwareUTC | None = None
    planned_end: AwareUTC | None = None
    assignment_group: str | None = Field(default=None, max_length=100)
    # Uygulayıcı; talep eden gibi kendi değişikliğini onaylayamaz
    assignee: Assignee = None
    affected_ci_ids: _IdList = Field(default_factory=list)
    linked_incident_ids: _IdList = Field(default_factory=list)

    @model_validator(mode="after")
    def _window(self) -> Self:
        _check_window(self.planned_start, self.planned_end)
        return self


class ChangeUpdate(BaseModel):
    """Taslak düzenleme: yalnızca gönderilen alanlar uygulanır (exclude_unset)."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    implementation_plan: str | None = None
    backout_plan: str | None = None
    test_plan: str | None = None
    planned_start: AwareUTC | None = None
    planned_end: AwareUTC | None = None
    assignment_group: str | None = Field(default=None, max_length=100)
    assignee: Assignee = None
    affected_ci_ids: _IdList | None = None
    linked_incident_ids: _IdList | None = None

    @model_validator(mode="after")
    def _window(self) -> Self:
        _check_window(self.planned_start, self.planned_end)
        return self


class ChangePatch(ChangeUpdate):
    """PATCH gövdesi: If-Match başlığı yerine sürüm gövdede de verilebilir."""

    expected_version: int | None = Field(default=None, ge=1)


class ChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    title: str
    description: str
    change_type: ChangeType
    state: ChangeState
    risk_level: RiskLevel | None
    risk_score: int | None
    implementation_plan: str
    backout_plan: str
    test_plan: str
    planned_start: UTCDateTime | None
    planned_end: UTCDateTime | None
    actual_start: UTCDateTime | None
    actual_end: UTCDateTime | None
    requested_by: str
    assignment_group: str | None
    assignee: str | None
    template_id: str | None
    affected_ci_ids: list[str]
    linked_incident_ids: list[str]
    implementation_outcome: str | None
    review_notes: str | None
    closed_at: UTCDateTime | None
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class TransitionRequest(BaseModel):
    to_state: ChangeState
    # If-Match başlığı yerine gövdede de verilebilir
    expected_version: int | None = Field(default=None, ge=1)
    # Denetim kaydına yazılan kısa not
    notes: str | None = Field(default=None, max_length=2000)
    # Uygulama sonrası gözden geçirme (PIR); acil/başarısız değişikliği kapatırken zorunlu
    review_notes: str | None = Field(default=None, max_length=10000)


class ApprovalCreate(BaseModel):
    decision: ApprovalDecision
    comment: str = Field(default="", max_length=2000)
    # Yalnızca doğrulama: onaylayan her zaman belirteçteki kullanıcıdır
    approver: str | None = Field(default=None, min_length=1, max_length=200)


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    change_id: str
    approver: str
    approver_roles: list[str]
    decision: ApprovalDecision
    comment: str
    created_at: UTCDateTime


class ApprovalStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    required: int = Field(description="Gereken geçerli onay (normal: CAB eşiği, acil: 1)")
    approvals: int = Field(description="Sayılan geçerli onaylar (acilde yalnızca change_manager)")
    rejections: int
    satisfied: bool


class ApprovalListRead(BaseModel):
    status: ApprovalStatusRead
    items: list[ApprovalRead]


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    max_risk: RiskLevel = RiskLevel.LOW
    allowed_ci_classes: list[CIClass] = Field(default_factory=list)
    implementation_plan: str = ""
    backout_plan: str = ""
    test_plan: str = ""
    active: bool = True


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    max_risk: RiskLevel | None = None
    allowed_ci_classes: list[CIClass] | None = None
    implementation_plan: str | None = None
    backout_plan: str | None = None
    test_plan: str | None = None
    active: bool | None = None


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    max_risk: RiskLevel
    allowed_ci_classes: list[CIClass]
    implementation_plan: str
    backout_plan: str
    test_plan: str
    active: bool
    created_by: str
    created_at: UTCDateTime
    updated_at: UTCDateTime


class FreezeWindowCreate(BaseModel):
    start_at: AwareUTC
    end_at: AwareUTC
    reason: str = Field(default="", max_length=2000)
    # Boş = tüm ortamlar
    environments: list[CIEnvironment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.end_at <= self.start_at:
            raise ValueError("Dondurma penceresinin bitişi başlangıcından sonra olmalıdır.")
        return self


class FreezeWindowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    start_at: UTCDateTime
    end_at: UTCDateTime
    reason: str
    environments: list[CIEnvironment]
    created_by: str
    created_at: UTCDateTime


class ScheduleConflictRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str = Field(description="freeze_window | change")
    ref_id: str
    ref_number: str | None = None
    start: UTCDateTime
    end: UTCDateTime
    description: str


class CalendarRead(BaseModel):
    changes: list[ChangeRead]
    freeze_windows: list[FreezeWindowRead]
