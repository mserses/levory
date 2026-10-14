"""Olay araçları: incident.* (services.incidents üzerinden; iş kuralları serviste kalır)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from serviszeka import policy
from serviszeka.domain.enums import (
    Impact,
    IncidentStatus,
    OnHoldReason,
    Priority,
    RiskClass,
    TicketKind,
    Urgency,
)
from serviszeka.domain.priority import calculate_priority
from serviszeka.models import Incident
from serviszeka.services import incidents as svc
from serviszeka.services import sla
from serviszeka.services.schemas import IncidentCreate, IncidentRead
from serviszeka.tools.builtin._common import (
    READERS,
    WRITERS,
    Args,
    AssignmentGroup,
    Category,
    Id,
    Service,
)
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolNotFoundError, ToolSpec

LINK = {"incident_id": "incident"}


async def _require(ctx: ToolContext, incident_id: str) -> Incident:
    incident = await svc.get_incident(ctx.session, incident_id)
    if incident is None:
        raise ToolNotFoundError(f"Olay bulunamadı: {incident_id}")
    return incident


async def _read(ctx: ToolContext, incident: Incident) -> dict[str, Any]:
    await ctx.session.refresh(incident)
    return IncidentRead.model_validate(incident).model_dump(mode="json")


async def _apply(ctx: ToolContext, incident_id: str, changes: dict[str, Any], expected: int | None):
    incident = await svc.apply_changes(
        ctx.session,
        incident_id,
        changes,
        ctx.actor.username,
        expected_version=expected,
        audit_details={"source": "tool"},
    )
    return await _read(ctx, incident)


class _Ref(Args):
    incident_id: Id = Field(description="Olay kimliği (id)")


class _Versioned(_Ref):
    expected_version: int | None = Field(
        default=None, ge=1, description="İyimser kilit: beklenen olay sürümü (uyuşmazsa 409)"
    )


# --- R0 ------------------------------------------------------------------------------------------


class SearchArgs(Args):
    q: str | None = Field(default=None, max_length=200, description="Numara/başlık/açıklamada ara")
    status: IncidentStatus | None = None
    priority: Priority | None = None
    assignment_group: str | None = Field(default=None, max_length=100)
    assignee: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


async def search(ctx: ToolContext, args: SearchArgs) -> dict[str, Any]:
    rows = await svc.list_incidents(
        ctx.session,
        status=args.status,
        priority=args.priority,
        assignment_group=args.assignment_group,
        assignee=args.assignee,
        q=args.q,
        limit=args.limit,
    )
    items = [IncidentRead.model_validate(r).model_dump(mode="json") for r in rows]
    return {"count": len(items), "items": items}


async def get(ctx: ToolContext, args: _Ref) -> dict[str, Any]:
    return await _read(ctx, await _require(ctx, args.incident_id))


# --- R1 (koşullu) ----------------------------------------------------------------------------------


class CreateArgs(Args):
    title: str = Field(min_length=1, max_length=300, description="Kısa başlık")
    description: str = Field(default="", max_length=20000)
    caller: str = Field(min_length=1, max_length=200, description="Arayan (kullanıcı adı)")
    impact: Impact | None = None
    urgency: Urgency | None = None
    service: Service = None
    category: Category = None
    kind: TicketKind | None = None


def _create_risk(ctx: ToolContext, args: CreateArgs) -> RiskClass:
    # Açılışta P1/P2 olay -> R2 (büyük olay insan kararıdır; Faz 1 P1/P2 kuralı)
    priority = calculate_priority(args.impact or Impact.LOW, args.urgency or Urgency.LOW)
    return RiskClass.R2 if priority in policy.URGENT_PRIORITIES else RiskClass.R1


async def create(ctx: ToolContext, args: CreateArgs) -> dict[str, Any]:
    incident = await svc.create_incident(
        ctx.session, IncidentCreate(**args.model_dump()), ctx.actor.username
    )
    return await _read(ctx, incident)


class ClassifyArgs(_Versioned):
    kind: TicketKind | None = None
    category: Category = None
    service: Service = None
    assignment_group: AssignmentGroup = None

    @model_validator(mode="after")
    def _some(self) -> Self:
        if not self.model_fields_set & {"kind", "category", "service", "assignment_group"}:
            raise ValueError("En az bir sınıflandırma alanı verilmeli.")
        return self


async def classify(ctx: ToolContext, args: ClassifyArgs) -> dict[str, Any]:
    fields = args.model_dump(
        include={"kind", "category", "service", "assignment_group"}, exclude_unset=True
    )
    return await _apply(ctx, args.incident_id, fields, args.expected_version)


class PriorityArgs(_Versioned):
    impact: Impact
    urgency: Urgency


async def _priority_risk(ctx: ToolContext, args: PriorityArgs) -> RiskClass:
    incident = await svc.get_incident(ctx.session, args.incident_id)
    if incident is None:
        return RiskClass.R1  # işleyici 404 döndürür
    proposed = calculate_priority(args.impact, args.urgency)
    # Yalnızca P3..P5 içinde ve düşürme olmayan değişiklik R1; P1/P2 ve düşürme R2 (insan onayı)
    low = policy.priority_change_risk(Priority(incident.priority), proposed)
    return RiskClass.R1 if low.value == "low" else RiskClass.R2


async def set_priority(ctx: ToolContext, args: PriorityArgs) -> dict[str, Any]:
    fields = {"impact": args.impact, "urgency": args.urgency}
    return await _apply(ctx, args.incident_id, fields, args.expected_version)


class AssignArgs(_Versioned):
    assignment_group: AssignmentGroup = None
    assignee: str | None = Field(default=None, max_length=200, description="Uzman kullanıcı adı")

    @model_validator(mode="after")
    def _some(self) -> Self:
        if not self.model_fields_set & {"assignment_group", "assignee"}:
            raise ValueError("assignment_group ya da assignee verilmeli.")
        return self


async def assign(ctx: ToolContext, args: AssignArgs) -> dict[str, Any]:
    incident = await _require(ctx, args.incident_id)
    fields: dict[str, Any] = args.model_dump(
        include={"assignment_group", "assignee"}, exclude_unset=True
    )
    if IncidentStatus(incident.status) == IncidentStatus.NEW:
        fields["status"] = IncidentStatus.ASSIGNED
    return await _apply(ctx, args.incident_id, fields, args.expected_version)


class LinkCIArgs(_Versioned):
    ci_ids: list[Id] = Field(min_length=1, max_length=100, description="Bağlanacak CI kimlikleri")
    replace: bool = Field(default=False, description="true: mevcut bağların yerine geçer")


async def link_ci(ctx: ToolContext, args: LinkCIArgs) -> dict[str, Any]:
    current = (
        []
        if args.replace
        else [c.id for c in await svc.list_affected_cis(ctx.session, args.incident_id)]
    )
    wanted = list(dict.fromkeys([*current, *args.ci_ids]))
    incident = await svc.set_affected_cis(
        ctx.session, args.incident_id, wanted, ctx.actor.username, args.expected_version
    )
    return await _read(ctx, incident)


class HoldArgs(_Versioned):
    hold_reason: OnHoldReason = Field(
        description="Bekleme nedeni: awaiting_caller/awaiting_vendor SLA saatini durdurur (R2)"
    )


def _hold_risk(ctx: ToolContext, args: HoldArgs) -> RiskClass:
    # SLA saatini durduran bekleme ihlali gizleyebilir -> insan onayı
    return RiskClass.R2 if sla.is_pausing_reason(args.hold_reason) else RiskClass.R1


async def set_on_hold(ctx: ToolContext, args: HoldArgs) -> dict[str, Any]:
    fields = {"status": IncidentStatus.ON_HOLD, "on_hold_reason": args.hold_reason}
    return await _apply(ctx, args.incident_id, fields, args.expected_version)


# --- R2 ---------------------------------------------------------------------------------------------


class ResolveArgs(_Versioned):
    resolution: str = Field(min_length=1, max_length=10000, description="Çözüm açıklaması")


async def resolve(ctx: ToolContext, args: ResolveArgs) -> dict[str, Any]:
    fields = {"status": IncidentStatus.RESOLVED, "resolution": args.resolution}
    return await _apply(ctx, args.incident_id, fields, args.expected_version)


def register_all() -> None:
    R0, R1, R2 = RiskClass.R0, RiskClass.R1, RiskClass.R2
    for spec in (
        ToolSpec("incident.search", "Olayları arar (numara/başlık/açıklama, durum, öncelik, grup).",
                 R0, SearchArgs, search, READERS),
        ToolSpec("incident.get", "Bir olayın ayrıntısını getirir (SLA vadeleri, bağlı CI'lar dahil).",
                 R0, _Ref, get, READERS, links=LINK),
        ToolSpec("incident.create", "Yeni olay/talep kaydı açar. Açılışta P1/P2 olacak kayıt R2'dir "
                 "(insan onayı).", R1, CreateArgs, create, WRITERS, risk_fn=_create_risk),
        ToolSpec("incident.classify", "Olayın türünü, kategorisini, hizmetini ve çözüm grubunu "
                 "sınıf listesinden belirler (öncelik değişmez).", R1, ClassifyArgs, classify,
                 WRITERS, links=LINK),
        ToolSpec("incident.set_priority", "Etki ve aciliyeti (dolayısıyla önceliği) değiştirir. "
                 "Yalnızca P3..P5 içinde yükseltme R1; P1/P2'ye yükseltme ve her düşürme R2.",
                 R1, PriorityArgs, set_priority, WRITERS, risk_fn=_priority_risk, links=LINK),
        ToolSpec("incident.assign", "Olayı çözüm grubuna ve/veya uzmana atar.", R1, AssignArgs,
                 assign, WRITERS, links=LINK),
        ToolSpec("incident.link_ci", "Olaydan etkilenen yapılandırma öğelerini (CI) bağlar; bekleyen "
                 "yapay zekâ önerileri bayatlar.", R1, LinkCIArgs, link_ci, WRITERS, links=LINK),
        ToolSpec("incident.set_on_hold", "Olayı beklemeye alır. SLA saatini durduran nedenler "
                 "(arayan/tedarikçi bekleniyor) R2'dir.", R1, HoldArgs, set_on_hold, WRITERS,
                 risk_fn=_hold_risk, links=LINK),
        ToolSpec("incident.resolve", "Olayı çözüm metniyle çözüldü olarak işaretler (her zaman "
                 "insan onayı).", R2, ResolveArgs, resolve, WRITERS, links=LINK),
    ):  # fmt: skip
        registry.register(spec)
