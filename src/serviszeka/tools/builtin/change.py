"""Değişiklik araçları: change.* (services.changes + agents.change_risk üzerinden).

Durum geçişleri ve CAB kuralları serviste kalır: `ai:` aktör geçiş yapamaz, bu yüzden geçiş içeren
araçlar (submit_for_cab, record_pir) R2'dir ve yalnızca onaylayan insanın kimliğiyle yürütülür.
"""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, ConfigDict, Field

from serviszeka.agents import change_risk
from serviszeka.domain.enums import ChangeState, ChangeType, RiskClass
from serviszeka.models import ChangeRequest
from serviszeka.services import changes as svc
from serviszeka.services.change_schemas import (
    ApprovalStatusRead,
    ChangeCreate,
    ChangeRead,
    FreezeWindowRead,
    ScheduleConflictRead,
    TemplateRead,
)
from serviszeka.services.schemas import AIDecisionRead
from serviszeka.tools.builtin._common import CHANGE_READERS, CHANGE_WRITERS, Args, Id
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolExecutionError, ToolNotFoundError, ToolSpec

LINK = {"change_id": "change"}


async def _require(ctx: ToolContext, change_id: str) -> ChangeRequest:
    change = await svc.get_change(ctx.session, change_id)
    if change is None:
        raise ToolNotFoundError(f"Değişiklik bulunamadı: {change_id}")
    return change


async def _read(ctx: ToolContext, change: ChangeRequest) -> dict[str, Any]:
    await ctx.session.refresh(change)
    return ChangeRead.model_validate(change).model_dump(mode="json")


class _Ref(Args):
    change_id: Id = Field(description="Değişiklik kimliği (id)")


class SearchArgs(Args):
    q: str | None = Field(default=None, max_length=200, description="Numara/başlıkta ara")
    state: ChangeState | None = None
    change_type: ChangeType | None = None
    ci_id: Id | None = None
    requested_by: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


async def search(ctx: ToolContext, args: SearchArgs) -> dict[str, Any]:
    rows = await svc.list_changes(
        ctx.session,
        state=args.state,
        change_type=args.change_type,
        ci_id=args.ci_id,
        requested_by=args.requested_by,
        q=args.q,
        limit=args.limit,
    )
    items = [ChangeRead.model_validate(r).model_dump(mode="json") for r in rows]
    return {"count": len(items), "items": items}


async def get(ctx: ToolContext, args: _Ref) -> dict[str, Any]:
    data = await _read(ctx, await _require(ctx, args.change_id))
    status = await svc.approval_status(ctx.session, args.change_id, settings=ctx.settings)
    data["approval_status"] = ApprovalStatusRead.model_validate(status).model_dump(mode="json")
    return data


class CalendarArgs(Args):
    start: AwareDatetime = Field(description="Başlangıç (saat dilimli ISO 8601)")
    end: AwareDatetime = Field(description="Bitiş (saat dilimli ISO 8601)")


async def calendar(ctx: ToolContext, args: CalendarArgs) -> dict[str, Any]:
    cal = await svc.calendar(ctx.session, args.start, args.end)
    return {
        "changes": [ChangeRead.model_validate(c).model_dump(mode="json") for c in cal.changes],
        "freeze_windows": [
            FreezeWindowRead.model_validate(f).model_dump(mode="json") for f in cal.freeze_windows
        ],
    }


async def check_conflicts(ctx: ToolContext, args: _Ref) -> dict[str, Any]:
    conflicts = await svc.find_conflicts(ctx.session, args.change_id)
    items = [ScheduleConflictRead.model_validate(c).model_dump(mode="json") for c in conflicts]
    return {"conflicts": items, "has_conflicts": bool(items)}


class TemplatesArgs(Args):
    active: bool | None = Field(default=True, description="Yalnızca etkin şablonlar (null: hepsi)")


async def list_templates(ctx: ToolContext, args: TemplatesArgs) -> dict[str, Any]:
    rows = await svc.list_templates(ctx.session, active=args.active)
    return {"items": [TemplateRead.model_validate(t).model_dump(mode="json") for t in rows]}


class DraftArgs(ChangeCreate):
    """Taslak RFC: değişiklik açma şemasıyla aynı (standart değişiklik etkin şablon ister)."""

    model_config = ConfigDict(extra="forbid")


async def draft_rfc(ctx: ToolContext, args: DraftArgs) -> dict[str, Any]:
    change = await svc.create_change(ctx.session, ChangeCreate(**args.model_dump()), ctx.actor)
    return await _read(ctx, change)


async def assess_risk(ctx: ToolContext, args: _Ref) -> dict[str, Any]:
    # Risk ajanı kendi politika + AIDecision + denetim zincirini uygular; yalnızca risk alanları
    decision = await change_risk.assess(
        ctx.session, await _require(ctx, args.change_id), ctx.get_llm(), ctx.settings, now=ctx.now
    )
    return AIDecisionRead.model_validate(decision).model_dump(mode="json")


class SubmitArgs(_Ref):
    notes: str | None = Field(default=None, max_length=2000, description="Denetim notu")


async def submit_for_cab(ctx: ToolContext, args: SubmitArgs) -> dict[str, Any]:
    """Taslağı değerlendirmeye, normal/acil değişikliği CAB/ECAB yetkilendirmesine gönderir."""
    change = await _require(ctx, args.change_id)
    if ChangeType(change.change_type) == ChangeType.STANDARD:
        raise ToolExecutionError("Standart değişiklik önceden onaylıdır; CAB'a gönderilmez.")
    if ChangeState(change.state) == ChangeState.DRAFT:
        change = await svc.transition(
            ctx.session, change.id, ChangeState.ASSESS, ctx.actor,
            notes=args.notes, settings=ctx.settings, now=ctx.now,
        )  # fmt: skip
    # Değerlendirmede standart uygunluğu yitirip normal'e dönen değişiklik de CAB'a gider
    change = await svc.transition(
        ctx.session, change.id, ChangeState.AUTHORIZE, ctx.actor,
        notes=args.notes, settings=ctx.settings, now=ctx.now,
    )  # fmt: skip
    return await _read(ctx, change)


class PIRArgs(_Ref):
    review_notes: str = Field(
        min_length=1, max_length=10000, description="PIR (gözden geçirme) notu"
    )


async def record_pir(ctx: ToolContext, args: PIRArgs) -> dict[str, Any]:
    """Uygulama sonrası gözden geçirmeyi (PIR) kaydeder ve değişikliği kapatır."""
    change = await svc.transition(
        ctx.session, args.change_id, ChangeState.CLOSED, ctx.actor,
        review_notes=args.review_notes, settings=ctx.settings, now=ctx.now,
    )  # fmt: skip
    return await _read(ctx, change)


def register_all() -> None:
    R0, R1, R2 = RiskClass.R0, RiskClass.R1, RiskClass.R2
    for spec in (
        ToolSpec("change.search", "Değişiklikleri arar (numara/başlık, durum, tür, CI, talep eden).",
                 R0, SearchArgs, search, CHANGE_READERS),
        ToolSpec("change.get", "Bir değişikliğin ayrıntısını ve CAB onay durumunu getirir.", R0, _Ref,
                 get, CHANGE_READERS),
        ToolSpec("change.calendar", "Aralıktaki değişiklik takvimi ve dondurma pencereleri.", R0,
                 CalendarArgs, calendar, CHANGE_READERS),
        ToolSpec("change.check_conflicts", "Değişikliğin planlanan penceresindeki dondurma ve aynı-CI "
                 "çakışmalarını listeler.", R0, _Ref, check_conflicts, CHANGE_READERS),
        ToolSpec("change.list_templates", "Standart (önceden onaylı) değişiklik şablonlarını "
                 "listeler.", R0, TemplatesArgs, list_templates, CHANGE_READERS),
        ToolSpec("change.draft_rfc", "Taslak değişiklik isteği (RFC) açar; durum taslakta kalır.",
                 R1, DraftArgs, draft_rfc, CHANGE_WRITERS),
        ToolSpec("change.assess_risk", "Değişiklik risk ajanını çalıştırır: yalnızca risk skoru "
                 "önerir, riski düşüren öneri change_manager onayı ister; durum değişmez.", R1, _Ref,
                 assess_risk, CHANGE_WRITERS, links=LINK),
        ToolSpec("change.submit_for_cab", "Değişikliği değerlendirmeye ve CAB/ECAB yetkilendirmesine "
                 "gönderir (insan onayı; onaylayan change_manager olmalı).", R2, SubmitArgs,
                 submit_for_cab, CHANGE_WRITERS, links=LINK),
        ToolSpec("change.record_pir", "Uygulama sonrası gözden geçirme (PIR) notunu kaydeder ve "
                 "değişikliği kapatır (insan onayı; change_manager).", R2, PIRArgs, record_pir,
                 CHANGE_WRITERS, links=LINK),
    ):  # fmt: skip
        registry.register(spec)
