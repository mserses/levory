"""Sorun yönetimi araçları: problem.* ve known_error.publish_workaround (services.problems üzerinden).

Yapay zekâ sorunu yalnızca önerir (create), olay bağlar, RCA'yı yalnızca taslağa yazar (record_rca ->
`rca_draft`) ve kalıcı çözüm için taslak RFC açar (propose_fix). Kapatma ve geçici çözümün KEDB'de
yayımlanması R2'dir (her zaman insan onayı); servis `ai:` aktörü ayrıca reddeder.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from serviszeka.auth import ROLE_AGENT, ROLE_APPROVER, ROLE_CHANGE_MANAGER
from serviszeka.domain.enums import ChangeType, ProblemState, RiskClass
from serviszeka.models import Problem
from serviszeka.services import changes as change_service
from serviszeka.services import problems as svc
from serviszeka.services.change_schemas import ChangeCreate, ChangeRead
from serviszeka.services.problem_schemas import RCA, IdList, ProblemCreate, ProblemRead
from serviszeka.tools.builtin._common import Args, AssignmentGroup, Id
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import (
    ToolConflictError,
    ToolContext,
    ToolExecutionError,
    ToolNotFoundError,
    ToolSpec,
)

# Sorun kayıtlarını agent ve approver okur/yazar (admin hepsi); requester göremez
PROBLEM_ROLES = frozenset({ROLE_AGENT, ROLE_APPROVER})
# Kalıcı çözüm RFC'si değişiklik açar: değişiklik yazarları
FIX_ROLES = frozenset({ROLE_AGENT, ROLE_CHANGE_MANAGER})


async def _require(ctx: ToolContext, problem_id: str) -> Problem:
    problem = await svc.get_problem(ctx.session, problem_id)
    if problem is None:
        raise ToolNotFoundError(f"Sorun bulunamadı: {problem_id}")
    return problem


def _read(problem: Problem) -> dict[str, Any]:
    return ProblemRead.model_validate(problem).model_dump(mode="json")


async def _call(coro):
    try:
        return await coro
    except svc.ProblemVersionConflictError as exc:
        raise ToolConflictError(str(exc)) from exc


def _existing(base: RiskClass):
    """Koşullu risk değil, ön koşul: sorun yoksa istek açılmadan 404 (risk temel sınıfta kalır)."""

    async def risk(ctx: ToolContext, args: Any) -> RiskClass:
        await _require(ctx, args.problem_id)
        return base

    return risk


class _Ref(Args):
    problem_id: Id = Field(description="Sorun kimliği (id)")


class SearchArgs(Args):
    q: str | None = Field(default=None, max_length=200, description="Numara/başlıkta ara")
    state: ProblemState | None = None
    incident_id: Id | None = None
    ci_id: Id | None = None
    limit: int = Field(default=20, ge=1, le=100)


async def search(ctx: ToolContext, args: SearchArgs) -> dict[str, Any]:
    rows = await svc.list_problems(
        ctx.session,
        state=args.state,
        q=args.q,
        incident_id=args.incident_id,
        ci_id=args.ci_id,
        limit=args.limit,
    )
    items = [_read(r) for r in rows]
    return {"count": len(items), "items": items}


class TrendsArgs(Args):
    window_days: int = Field(default=30, ge=1, le=365, description="Geriye dönük gün sayısı")


async def detect_trends(ctx: ToolContext, args: TrendsArgs) -> dict[str, Any]:
    from serviszeka.agents import problem as agent

    clusters = await agent.detect_trends(ctx.session, args.window_days, now=ctx.now)
    return {"window_days": args.window_days, "clusters": [c.to_dict() for c in clusters]}


class CreateArgs(ProblemCreate):
    """Sorun açma şemasıyla aynı (bilinmeyen alan reddedilir)."""


async def create(ctx: ToolContext, args: CreateArgs) -> dict[str, Any]:
    # Onaylı istekte kaydı onaylayan açar; tespit eden isteği açan (ör. ai:problem) kalır
    detected_by = ctx.action_request.requested_by if ctx.action_request else None
    problem = await svc.create_problem(
        ctx.session, ProblemCreate(**args.model_dump()), ctx.actor, detected_by=detected_by
    )
    return _read(problem)


class LinkIncidentsArgs(_Ref):
    incident_ids: IdList = Field(min_length=1, description="Bağlanacak olay kimlikleri")


async def link_incidents(ctx: ToolContext, args: LinkIncidentsArgs) -> dict[str, Any]:
    problem = await _call(
        svc.link_incidents(ctx.session, args.problem_id, args.incident_ids, ctx.actor)
    )
    return _read(problem)


class RecordRCAArgs(_Ref):
    rca: RCA | None = Field(
        default=None,
        description="Yapılandırılmış RCA (5_why / timeline); verilmezse LLM taslak üretir",
    )


async def record_rca(ctx: ToolContext, args: RecordRCAArgs) -> dict[str, Any]:
    """Yalnızca `rca_draft` yazar; kesin RCA insan kabulüyle (`/problems/{id}/rca/accept`)."""
    rca, source = args.rca, "tool"
    if rca is None:
        from serviszeka.agents import problem as agent

        problem = await _require(ctx, args.problem_id)
        rca, _, _ = await agent.generate_rca(problem, ctx.get_llm(), ctx.settings)
        source = "llm"
    problem = await _call(
        svc.set_rca_draft(ctx.session, args.problem_id, rca, ctx.actor, source=source)
    )
    return _read(problem)


class ProposeFixArgs(_Ref):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    change_type: ChangeType = Field(default=ChangeType.NORMAL, description="normal / acil / standart")
    template_id: Id | None = Field(default=None, description="Standart değişiklik şablonu")
    implementation_plan: str = Field(default="", max_length=20000)
    backout_plan: str = Field(default="", max_length=20000)
    test_plan: str = Field(default="", max_length=20000)
    assignment_group: AssignmentGroup


def _fix_description(problem: Problem) -> str:
    root = problem.root_cause
    if not root and (problem.rca_draft or {}).get("root_cause"):
        root = f"{problem.rca_draft['root_cause']} (taslak, insan onayı bekliyor)"
    lines = [
        f"Sorun: {problem.number} — {problem.title}",
        f"Kök neden: {root or 'henüz belirlenmedi'}",
    ]
    if problem.workaround:
        lines.append(f"Geçici çözüm: {problem.workaround}")
    if problem.incidents:
        lines.append(f"Bağlı olaylar: {', '.join(i.number for i in problem.incidents)}")
    return "\n".join(lines)


async def propose_fix(ctx: ToolContext, args: ProposeFixArgs) -> dict[str, Any]:
    """Kalıcı çözüm için taslak RFC açar (durum taslakta kalır) ve soruna bağlar."""
    problem = await _require(ctx, args.problem_id)
    if ProblemState(problem.state) == ProblemState.CLOSED:
        raise ToolExecutionError(f"{problem.number} kapalı; kalıcı çözüm önerilemez.")
    change = await change_service.create_change(
        ctx.session,
        ChangeCreate(
            title=args.title or f"Kalıcı çözüm: {problem.number} {problem.title}"[:300],
            description=_fix_description(problem),
            change_type=args.change_type,
            template_id=args.template_id,
            implementation_plan=args.implementation_plan,
            backout_plan=args.backout_plan,
            test_plan=args.test_plan,
            assignment_group=args.assignment_group or problem.assignment_group,
            affected_ci_ids=problem.affected_ci_ids,
            linked_incident_ids=problem.incident_ids,
        ),
        ctx.actor,
    )
    problem = await _call(svc.link_changes(ctx.session, problem.id, [change.id], ctx.actor))
    await ctx.session.refresh(change)
    return {
        "problem": _read(problem),
        "change": ChangeRead.model_validate(change).model_dump(mode="json"),
    }


class CloseArgs(_Ref):
    resolution: str = Field(min_length=1, max_length=10000, description="Kapanış notu")


async def close(ctx: ToolContext, args: CloseArgs) -> dict[str, Any]:
    problem = await _call(
        svc.transition(
            ctx.session, args.problem_id, ProblemState.CLOSED, ctx.actor, note=args.resolution
        )
    )
    return _read(problem)


class PublishWorkaroundArgs(_Ref):
    title: str | None = Field(default=None, min_length=1, max_length=300, description="KEDB başlığı")


async def publish_workaround(ctx: ToolContext, args: PublishWorkaroundArgs) -> dict[str, Any]:
    problem = await _call(
        svc.publish_known_error(ctx.session, args.problem_id, ctx.actor, title=args.title)
    )
    return _read(problem)


def register_all() -> None:
    R0, R1, R2 = RiskClass.R0, RiskClass.R1, RiskClass.R2
    for spec in (
        ToolSpec("problem.search", "Sorun kayıtlarını arar (numara/başlık, durum, bağlı olay, CI).",
                 R0, SearchArgs, search, PROBLEM_ROLES),
        ToolSpec("problem.detect_trends", "Olay eğilimlerini bulur: aynı CI + kategori ve benzer "
                 "başlık kümeleri (deterministik, salt okuma).", R0, TrendsArgs, detect_trends,
                 PROBLEM_ROLES),
        ToolSpec("problem.create", "Sorun kaydı açar (durum 'new'); olay ve CI bağlarıyla.", R1,
                 CreateArgs, create, PROBLEM_ROLES),
        ToolSpec("problem.link_incidents", "Olayları soruna bağlar.", R1, LinkIncidentsArgs,
                 link_incidents, PROBLEM_ROLES, risk_fn=_existing(R1)),
        ToolSpec("problem.record_rca", "Kök neden analizini (5 Neden / zaman çizelgesi) yalnızca "
                 "TASLAK olarak yazar; verilmezse LLM taslak üretir. Kesin RCA insan kabulüyle.", R1,
                 RecordRCAArgs, record_rca, PROBLEM_ROLES, risk_fn=_existing(R1)),
        ToolSpec("problem.propose_fix", "Kalıcı çözüm için taslak değişiklik isteği (RFC) açar ve "
                 "soruna bağlar; RFC taslakta kalır.", R1, ProposeFixArgs, propose_fix, FIX_ROLES,
                 risk_fn=_existing(R1)),
        ToolSpec("problem.close", "Sorunu kapanış notuyla kapatır (insan onayı).", R2, CloseArgs,
                 close, PROBLEM_ROLES, risk_fn=_existing(R2)),
        ToolSpec("known_error.publish_workaround", "Sorunun geçici çözümünü bilinen hata (KEDB) "
                 "makalesi olarak yayımlar; herkes görür (insan onayı).", R2, PublishWorkaroundArgs,
                 publish_workaround, PROBLEM_ROLES, risk_fn=_existing(R2)),
    ):  # fmt: skip
        registry.register(spec)
