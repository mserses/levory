"""Sürekli iyileştirme (CSI) araçları: csi.list, csi.detect_signals, csi.create_improvement,
csi.measure_improvement (services.improvements üzerinden).

Yapay zekâ yalnızca fikir açar (R1, durum `idea`; taban değeri veremez, KPI'dan hesaplanır) ve uygulaması bitmiş iyileştirmenin KPI'sını
ölçer (R1, deterministik hesap). Onay, başlatma, uygulandı işareti, kapatma ve red araç olarak
yoktur: yalnızca insan, `/improvements` API'si (servis `ai:` aktörü ayrıca reddeder).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from serviszeka.auth import AI_PREFIX, ROLE_AGENT, ROLE_APPROVER
from serviszeka.domain.enums import ImprovementSource, ImprovementState, RiskClass
from serviszeka.services import improvements as svc
from serviszeka.services.improvement_schemas import (
    ImprovementCreate,
    ImprovementRead,
    KPIKey,
)
from serviszeka.tools.builtin._common import Args, Id
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import (
    ToolConflictError,
    ToolContext,
    ToolNotFoundError,
    ToolSpec,
    ToolValidationError,
)

# İyileştirme kaydını agent ve approver okur/açar (admin hepsi); requester göremez
CSI_ROLES = frozenset({ROLE_AGENT, ROLE_APPROVER})


def _read(imp) -> dict[str, Any]:
    return ImprovementRead.model_validate(imp).model_dump(mode="json")


async def _call(coro):
    try:
        return await coro
    except (svc.ImprovementVersionConflictError, svc.ImprovementNotReadyError) as exc:
        raise ToolConflictError(str(exc)) from exc


def _existing(base: RiskClass):
    """Ön koşul: kayıt yoksa istek açılmadan 404 (risk temel sınıfta kalır)."""

    async def risk(ctx: ToolContext, args: Any) -> RiskClass:
        if await svc.get_improvement(ctx.session, args.improvement_id) is None:
            raise ToolNotFoundError(f"İyileştirme bulunamadı: {args.improvement_id}")
        return base

    return risk


class ListArgs(Args):
    state: ImprovementState | None = None
    source: ImprovementSource | None = None
    kpi_key: KPIKey | None = None
    q: str | None = Field(default=None, max_length=200, description="Numara/başlıkta ara")
    open_only: bool = False
    limit: int = Field(default=20, ge=1, le=100)


async def list_improvements(ctx: ToolContext, args: ListArgs) -> dict[str, Any]:
    rows = await svc.list_improvements(
        ctx.session,
        state=args.state,
        source=args.source,
        kpi_key=args.kpi_key,
        q=args.q,
        open_only=args.open_only,
        limit=args.limit,
    )
    items = [_read(r) for r in rows]
    return {"count": len(items), "items": items}


class SignalsArgs(Args):
    window_days: int = Field(default=30, ge=7, le=180, description="Sinyal penceresi (gün)")


async def detect_signals(ctx: ToolContext, args: SignalsArgs) -> dict[str, Any]:
    from serviszeka.agents import improvement as agent

    signals = await agent.detect_signals(ctx.session, window_days=args.window_days, now=ctx.now)
    return {
        "window_days": args.window_days,
        "signals": [s.to_read().model_dump(mode="json") for s in signals],
    }


class CreateArgs(ImprovementCreate):
    """İyileştirme açma şemasıyla aynı (durum/onay alanı yok; bilinmeyen alan reddedilir)."""


def _ai_origin(ctx: ToolContext) -> bool:
    """Çağrı yapay zekâdan mı geliyor (doğrudan ya da onaylanan yapay zekâ isteği)."""
    requested_by = ctx.action_request.requested_by if ctx.action_request else ""
    return ctx.actor.is_ai or str(requested_by or "").startswith(AI_PREFIX)


async def create(ctx: ToolContext, args: CreateArgs) -> dict[str, Any]:
    # MTTR/KPI iddiası yalnızca veriden: yapay zekâ taban değer veremez, taban KPI'dan hesaplanır
    if args.baseline_value is not None and _ai_origin(ctx):
        raise ToolValidationError(
            "Yapay zekâ taban değeri veremez; taban bağlı KPI'dan hesaplanır (baseline_value boş "
            "bırakılmalı)."
        )
    # Onaylı istekte kaydı onaylayan açar; tespit eden isteği açan (ör. ai:csi) kalır
    detected_by = ctx.action_request.requested_by if ctx.action_request else None
    imp = await svc.create_improvement(
        ctx.session,
        ImprovementCreate(**args.model_dump()),
        ctx.actor,
        detected_by=detected_by,
        now=ctx.now,
        settings=ctx.settings,
    )
    return _read(imp)


class MeasureArgs(Args):
    improvement_id: Id = Field(description="İyileştirme kimliği (id)")


async def measure(ctx: ToolContext, args: MeasureArgs) -> dict[str, Any]:
    imp = await _call(svc.measure(ctx.session, args.improvement_id, ctx.actor, now=ctx.now))
    return _read(imp)


def register_all() -> None:
    R0, R1 = RiskClass.R0, RiskClass.R1
    for spec in (
        ToolSpec("csi.list", "Sürekli iyileştirme kaydını listeler (durum, kaynak, KPI, arama).",
                 R0, ListArgs, list_improvements, CSI_ROLES),
        ToolSpec("csi.detect_signals", "KPI eğilimleri, SLA tahmini, bilinen hatalar ve PIR'lerden "
                 "iyileştirme sinyallerini kanıt sayılarıyla bulur (deterministik, salt okuma).",
                 R0, SignalsArgs, detect_signals, CSI_ROLES),
        ToolSpec("csi.create_improvement", "Sürekli iyileştirme fikri açar (durum 'idea'; onay "
                 "insandır). KPI'ya bağlıysa taban değer hesaplanır.", R1, CreateArgs, create,
                 CSI_ROLES),
        ToolSpec("csi.measure_improvement", "Uygulaması biten iyileştirmenin KPI'sını önce/sonra "
                 "pencereleriyle ölçer, ölçülen değeri ve hedefe ulaşılıp ulaşılmadığını yazar.",
                 R1, MeasureArgs, measure, CSI_ROLES, risk_fn=_existing(R1)),
    ):  # fmt: skip
        registry.register(spec)
