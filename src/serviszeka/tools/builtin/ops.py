"""CMDB, SLA, nöbetçi bildirimi ve denetim araçları (cmdb.*, sla.*, notify.*, audit.*)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select

from serviszeka import audit, notifications
from serviszeka.domain.enums import CIClass, CIEnvironment, CIStatus, RiskClass
from serviszeka.models import AuditLog
from serviszeka.privacy import mask_pii
from serviszeka.services import cmdb, sla
from serviszeka.services import incidents as incident_service
from serviszeka.services.cmdb_schemas import CIRead, CIRelationshipRead, ImpactRead
from serviszeka.services.schemas import SLAAlertRead
from serviszeka.tools.builtin._common import ADMINS, CHANGE_READERS, READERS, WRITERS, Args, Id
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolNotFoundError, ToolSpec

# --- CMDB ------------------------------------------------------------------------------------------


class CISearchArgs(Args):
    q: str | None = Field(default=None, max_length=200, description="Ad içinde ara")
    ci_class: CIClass | None = None
    environment: CIEnvironment | None = None
    status: CIStatus | None = None
    owner_group: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=200)


async def search_ci(ctx: ToolContext, args: CISearchArgs) -> dict[str, Any]:
    rows = await cmdb.list_cis(
        ctx.session,
        ci_class=args.ci_class,
        environment=args.environment,
        status=args.status,
        owner_group=args.owner_group,
        q=args.q,
        limit=args.limit,
    )
    items = [CIRead.model_validate(r).model_dump(mode="json") for r in rows]
    return {"count": len(items), "items": items}


class CIRef(Args):
    ci_id: Id = Field(description="CI kimliği (id)")


async def get_ci(ctx: ToolContext, args: CIRef) -> dict[str, Any]:
    ci = await cmdb.get_ci(ctx.session, args.ci_id)
    if ci is None:
        raise ToolNotFoundError(f"CI bulunamadı: {args.ci_id}")
    data = CIRead.model_validate(ci).model_dump(mode="json")
    rels = await cmdb.list_relationships(ctx.session, ci.id)
    data["relationships"] = [
        CIRelationshipRead.model_validate(r).model_dump(mode="json") for r in rels
    ]
    return data


class ImpactArgs(Args):
    ci_ids: list[Id] = Field(min_length=1, max_length=200)
    max_depth: int = Field(default=5, ge=1, le=20)


async def impact_analysis(ctx: ToolContext, args: ImpactArgs) -> dict[str, Any]:
    result = await cmdb.impact_analysis(ctx.session, args.ci_ids, args.max_depth)
    return ImpactRead.from_analysis(result).model_dump(mode="json")


# --- SLA -------------------------------------------------------------------------------------------


class SLAStatusArgs(Args):
    incident_id: Id


async def get_status(ctx: ToolContext, args: SLAStatusArgs) -> dict[str, Any]:
    incident = await incident_service.get_incident(ctx.session, args.incident_id)
    if incident is None:
        raise ToolNotFoundError(f"Olay bulunamadı: {args.incident_id}")
    alerts = sla.evaluate_incident(incident, ctx.now)

    def ts(value):
        return sla.ensure_utc(value).isoformat() if value else None

    return {
        "incident_id": incident.id,
        "number": incident.number,
        "priority": incident.priority.value,
        "status": incident.status.value,
        "response_due_at": ts(incident.response_due_at),
        "resolve_due_at": ts(incident.resolve_due_at),
        "responded_at": ts(incident.responded_at),
        "resolved_at": ts(incident.resolved_at),
        "paused": sla.is_paused(incident),
        "sla_paused_seconds": incident.sla_paused_seconds or 0,
        "alerts": [SLAAlertRead.model_validate(a).model_dump(mode="json") for a in alerts],
    }


class AtRiskArgs(Args):
    state: Literal["at_risk", "breached"] | None = Field(default=None, description="Süzgeç")
    limit: int = Field(default=50, ge=1, le=200)


async def list_at_risk(ctx: ToolContext, args: AtRiskArgs) -> dict[str, Any]:
    alerts = await sla.check_breaches(ctx.session, ctx.now)
    if args.state:
        alerts = [a for a in alerts if a.state.value == args.state]
    items = [SLAAlertRead.model_validate(a).model_dump(mode="json") for a in alerts[: args.limit]]
    return {"count": len(items), "items": items}


# --- nöbetçi ---------------------------------------------------------------------------------------

ONCALL_PAGE_EVENT = "oncall.page"


class PageArgs(Args):
    message: str = Field(min_length=1, max_length=1000, description="Nöbetçiye iletilecek mesaj")
    incident_id: Id | None = None
    severity: Literal["high", "critical"] = "high"


async def oncall_page(ctx: ToolContext, args: PageArgs) -> dict[str, Any]:
    """Nöbetçi uyarısını outbox'a ekler; gönderim commit sonrasıdır (notifications)."""
    number = None
    if args.incident_id:
        incident = await incident_service.get_incident(ctx.session, args.incident_id)
        if incident is None:
            raise ToolNotFoundError(f"Olay bulunamadı: {args.incident_id}")
        number = incident.number
    payload = {
        "message": mask_pii(args.message),
        "severity": args.severity,
        "incident_id": args.incident_id,
        "incident_number": number,
        "requested_by": ctx.actor.username,
    }
    notifications.stage(
        ctx.session,
        notifications.PendingNotification(
            event=ONCALL_PAGE_EVENT,
            payload=payload,
            actor=ctx.actor.username,
            entity_type="incident" if args.incident_id else "tool",
            entity_id=args.incident_id or "notify.oncall_page",
            details={"severity": args.severity},
        ),
    )
    return {"status": "staged", "event": ONCALL_PAGE_EVENT, "payload": payload}


# --- denetim ---------------------------------------------------------------------------------------


class NoArgs(Args):
    pass


async def verify_chain(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    return {"valid": await audit.verify_chain(ctx.session)}


class AuditSearchArgs(Args):
    actor: str | None = Field(default=None, max_length=200)
    action: str | None = Field(default=None, max_length=100)
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=50, ge=1, le=200)


async def audit_search(ctx: ToolContext, args: AuditSearchArgs) -> dict[str, Any]:
    stmt = select(AuditLog)
    for column, value in (
        (AuditLog.actor, args.actor),
        (AuditLog.action, args.action),
        (AuditLog.entity_type, args.entity_type),
        (AuditLog.entity_id, args.entity_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    rows = (await ctx.session.scalars(stmt.order_by(AuditLog.seq.desc()).limit(args.limit))).all()
    items = [
        {
            "seq": r.seq,
            "occurred_at": sla.ensure_utc(r.occurred_at).isoformat(),
            "actor": r.actor,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "details": r.details,
            "hash": r.hash,
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


def register_all() -> None:
    R0, R1 = RiskClass.R0, RiskClass.R1
    for spec in (
        ToolSpec("cmdb.search_ci", "Yapılandırma öğelerini (CI) arar.", R0, CISearchArgs, search_ci,
                 CHANGE_READERS),
        ToolSpec("cmdb.get_ci", "Bir CI'ı ve ilişkilerini getirir.", R0, CIRef, get_ci,
                 CHANGE_READERS),
        ToolSpec("cmdb.impact_analysis", "CI'lar değişirse etkilenecek CI'ları ve iş hizmetlerini "
                 "hesaplar (sınırda kesilirse truncated=true).", R0, ImpactArgs, impact_analysis,
                 CHANGE_READERS),
        ToolSpec("sla.get_status", "Bir olayın SLA vadelerini, duraklama durumunu ve uyarılarını "
                 "getirir.", R0, SLAStatusArgs, get_status, READERS),
        ToolSpec("sla.list_at_risk", "Açık olaylarda riskteki ve ihlal edilmiş SLA'ları listeler "
                 "(önce ihlaller).", R0, AtRiskArgs, list_at_risk, READERS),
        ToolSpec("notify.oncall_page", "Nöbetçiyi uyarır (mesaj maskelenir; gönderim kayıt kalıcı "
                 "olduktan sonra).", R1, PageArgs, oncall_page, WRITERS,
                 links={"incident_id": "incident"}),
        ToolSpec("audit.verify_chain", "Hash zincirli denetim günlüğünün bütünlüğünü doğrular.", R0,
                 NoArgs, verify_chain, ADMINS),
        ToolSpec("audit.search", "Denetim günlüğünde aktör/eylem/varlığa göre arar (en yeni önce).",
                 R0, AuditSearchArgs, audit_search, ADMINS),
    ):  # fmt: skip
        registry.register(spec)
