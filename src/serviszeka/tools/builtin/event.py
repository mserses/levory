"""Faz 4 izleme / olay yönetimi araçları: event.* (services/events.py üzerinden).

- `event.list_alerts` (R0), `event.correlate` (R0): yalnızca okuma.
- `event.acknowledge` (R1): grubu üstlenildi olarak işaretler.
- `event.create_incident` (R1; olay **P1/P2 açılacaksa R2**): grup başına tek olay açar ya da
  mevcut olaya çalışma notu + CI bağı ekler. Olayı asla çözmez (`incident.resolve` R2 kalır).
- `event.silence` (R2): grubu süreli susturur (yeni uyarılar olay/not tetiklemez).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from serviszeka import policy
from serviszeka.domain.enums import EventGroupState, EventSeverity, EventStatus, RiskClass
from serviszeka.models import EventGroup
from serviszeka.services import events as svc
from serviszeka.services.event_schemas import EventGroupRead, EventRead
from serviszeka.tools.builtin._common import READERS, WRITERS, Args, Id
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolNotFoundError, ToolSpec


async def _require(ctx: ToolContext, group_id: str) -> EventGroup:
    group = await ctx.session.get(EventGroup, group_id)
    if group is None:
        raise ToolNotFoundError(f"Olay grubu bulunamadı: {group_id}")
    return group


def _group(group: EventGroup) -> dict[str, Any]:
    return EventGroupRead.model_validate(group).model_dump(mode="json")


# Eylem isteğinin indeksli `event_group_id` sütunu (olay ajanı bekleyen isteği SQL'de arar)
LINK = {"group_id": "event_group"}


class _GroupRef(Args):
    group_id: Id = Field(description="Uyarı grubu kimliği (id)")


# --- R0 ---------------------------------------------------------------------------------------


class ListArgs(Args):
    status: EventStatus | None = None
    severity: EventSeverity | None = None
    group_id: Id | None = None
    ci_id: Id | None = None
    limit: int = Field(default=50, ge=1, le=200)


async def list_alerts(ctx: ToolContext, args: ListArgs) -> dict[str, Any]:
    rows = await svc.list_events(
        ctx.session,
        status=args.status,
        severity=args.severity,
        group_id=args.group_id,
        ci_id=args.ci_id,
        limit=args.limit,
    )
    items = [EventRead.model_validate(r).model_dump(mode="json") for r in rows]
    return {"count": len(items), "items": items}


class CorrelateArgs(Args):
    group_id: Id | None = Field(default=None, description="Boşsa açık gruplar")
    limit: int = Field(default=20, ge=1, le=100)


async def correlate(ctx: ToolContext, args: CorrelateArgs) -> dict[str, Any]:
    if args.group_id:
        groups = [await _require(ctx, args.group_id)]
    else:
        groups = await svc.list_groups(ctx.session, state=EventGroupState.OPEN, limit=args.limit)
    items = [
        (await svc.correlation(ctx.session, g, settings=ctx.settings, now=ctx.now)).model_dump(
            mode="json"
        )
        for g in groups
    ]
    return {"count": len(items), "items": items}


# --- R1 ---------------------------------------------------------------------------------------


class AckArgs(_GroupRef):
    note: str = Field(default="", max_length=500)


async def acknowledge(ctx: ToolContext, args: AckArgs) -> dict[str, Any]:
    group = await _require(ctx, args.group_id)
    await svc.acknowledge(ctx.session, group, ctx.actor.username, note=args.note, now=ctx.now)
    return _group(group)


async def _create_risk(ctx: ToolContext, args: _GroupRef) -> RiskClass:
    group = await ctx.session.get(EventGroup, args.group_id)
    if group is None or group.incident_id is not None:
        return RiskClass.R1  # işleyici 404 döndürür / mevcut olaya yalnızca not
    plan = await svc.incident_plan(ctx.session, group)
    # Faz 1 kuralı: P1/P2 olay açmak insan kararıdır
    return RiskClass.R2 if plan.priority in policy.URGENT_PRIORITIES else RiskClass.R1


async def create_incident(ctx: ToolContext, args: _GroupRef) -> dict[str, Any]:
    group = await _require(ctx, args.group_id)
    return await svc.open_or_update_incident(ctx.session, group, ctx.actor.username, now=ctx.now)


# --- R2 ---------------------------------------------------------------------------------------


class SilenceArgs(_GroupRef):
    minutes: int = Field(ge=5, le=10080, description="Susturma süresi (dk, en fazla 7 gün)")
    comment: str = Field(min_length=1, max_length=500, description="Gerekçe")


async def silence(ctx: ToolContext, args: SilenceArgs) -> dict[str, Any]:
    group = await _require(ctx, args.group_id)
    await svc.silence(
        ctx.session,
        group,
        ctx.actor.username,
        minutes=args.minutes,
        comment=args.comment,
        now=ctx.now,
    )
    return _group(group)


def register_all() -> None:
    R0, R1, R2 = RiskClass.R0, RiskClass.R1, RiskClass.R2
    for spec in (
        ToolSpec("event.list_alerts", "İzleme uyarılarını listeler (durum, önem, grup, CI).",
                 R0, ListArgs, list_alerts, READERS),
        ToolSpec("event.correlate", "Uyarı gruplarının bağıntısını açıklar: kök CI ve her uyarıya "
                 "CMDB bağımlılık yolu, önerilen öncelik, bekleyen istek.", R0, CorrelateArgs,
                 correlate, READERS),
        ToolSpec("event.acknowledge", "Uyarı grubunu üstlenildi olarak işaretler.", R1, AckArgs,
                 acknowledge, WRITERS, links=LINK),
        ToolSpec("event.create_incident", "Uyarı grubu için tek olay açar ya da mevcut olaya "
                 "çalışma notu ekler (asla çözmez). P1/P2 olay açılacaksa R2 (insan onayı).",
                 R1, _GroupRef, create_incident, WRITERS, risk_fn=_create_risk, links=LINK),
        ToolSpec("event.silence", "Uyarı grubunu belirli bir süre susturur (her zaman insan "
                 "onayı).", R2, SilenceArgs, silence, WRITERS, links=LINK),
    ):  # fmt: skip
        registry.register(spec)
