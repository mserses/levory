"""Faz 3 araç kayıt defteri ve tek yürütme boru hattı (`tools.execute_tool`)."""

from __future__ import annotations

import re

import pytest
from _tooling import (
    ADMIN,
    AGENT,
    AI,
    LIVE,
    REQUESTER,
    SHADOW,
    make_test_tool,
    registered,
    seed,
    spy_handler,
)
from sqlalchemy import func, select

from serviszeka import policy
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import DecisionOutcome, IncidentStatus, Priority, RiskClass
from serviszeka.models import ActionRequest, AIDecision, AuditLog, Incident, ToolInvocation
from serviszeka.tools import (
    RESERVED_ARGS,
    IdempotencyConflictError,
    ToolForbiddenError,
    ToolNotFoundError,
    ToolStatus,
    ToolUnavailableError,
    ToolValidationError,
    as_tool_actor,
    execute_tool,
    registry,
)

# Ürün sahibiyle tasarlanan katalogdan bugün kodda karşılığı olan araçlar
EXPECTED_TOOLS = {
    "incident.search", "incident.get", "incident.create", "incident.classify",
    "incident.set_priority", "incident.assign", "incident.link_ci", "incident.set_on_hold",
    "incident.resolve",
    "change.search", "change.get", "change.calendar", "change.check_conflicts",
    "change.list_templates", "change.draft_rfc", "change.assess_risk", "change.submit_for_cab",
    "change.record_pir",
    "kb.search", "kb.get", "kb.draft_article", "kb.update_draft", "kb.publish", "kb.retire",
    "known_error.create",
    "cmdb.search_ci", "cmdb.get_ci", "cmdb.impact_analysis",
    "sla.get_status", "sla.list_at_risk",
    "notify.oncall_page",
    "audit.verify_chain", "audit.search",
}  # fmt: skip

NAME_RE = re.compile(r"^[a-z][a-z_]*\.[a-z]+(_[a-z]+)*$")


def test_registry_contains_catalogue_tools():
    assert EXPECTED_TOOLS <= set(registry.names())


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_tool_spec_is_well_formed(name):
    spec = registry.get(name)
    assert NAME_RE.match(spec.name), spec.name
    assert spec.domain == name.split(".")[0] and spec.verb == name.split(".", 1)[1]
    assert isinstance(spec.risk_class, RiskClass)
    assert spec.description.strip() and spec.roles
    # Girdi şeması: JSON şeması üretilebilir, ayrılmış zarf alanlarını kullanmaz
    schema = spec.input_schema()
    assert schema["type"] == "object"
    assert not set(schema.get("properties", {})) & RESERVED_ARGS


def test_register_rejects_bad_specs():
    calls: list = []
    with pytest.raises(ValueError):
        registry.register(make_test_tool("Kotu Ad", RiskClass.R1, calls))
    with pytest.raises(ValueError):
        registry.register(registry.get("incident.get"))  # yinelenen ad


def test_as_tool_actor_maps_ai_agent_role():
    assert as_tool_actor(CurrentUser("copilot", frozenset({"ai_agent", "admin"}))) == CurrentUser(
        "ai:copilot", frozenset({"ai_agent"})
    )
    assert as_tool_actor(AI) == AI
    assert as_tool_actor(AGENT) == AGENT


# --- R0 ------------------------------------------------------------------------------------


async def test_r0_runs_for_human_and_ai(session):
    fx = await seed(session)
    r = await execute_tool(session, AGENT, "incident.get", {"incident_id": fx.incident_id})
    assert r.status == ToolStatus.EXECUTED and r.risk_class == RiskClass.R0
    assert r.result["number"] == fx.incident_number
    r = await execute_tool(session, AI, "kb.search", {"query": "vpn"})
    assert r.status == ToolStatus.EXECUTED
    assert r.result["hits"][0]["article_id"] == fx.article_id
    # Yapay zekânın okuması da denetime yazılır (KVKK izlenebilirliği); insan okuması yazılmaz
    actions = (
        await session.scalars(select(AuditLog.actor).where(AuditLog.action == "tool.read"))
    ).all()
    assert actions == ["ai:copilot"]


async def test_unknown_tool_and_forbidden_roles(session):
    await seed(session)
    with pytest.raises(ToolNotFoundError):
        await execute_tool(session, AGENT, "incident.delete", {})
    with pytest.raises(ToolForbiddenError):
        await execute_tool(session, AGENT, "audit.verify_chain", {})  # yalnızca admin
    with pytest.raises(ToolForbiddenError):
        await execute_tool(session, REQUESTER, "incident.search", {})
    # ai_agent rolü olmayan "ai:" aktör hiçbir aracı çağıramaz
    with pytest.raises(ToolForbiddenError):
        await execute_tool(
            session, CurrentUser("ai:x", frozenset({"admin"})), "kb.search", {"query": "a"}
        )
    r = await execute_tool(session, ADMIN, "audit.verify_chain", {})
    assert r.result == {"valid": True}


async def test_validation_errors_are_reported_before_any_write(session):
    fx = await seed(session)
    before = await session.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(ToolValidationError) as exc:
        await execute_tool(session, AI, "incident.resolve", {"incident_id": fx.incident_id})
    assert exc.value.errors  # resolution eksik
    with pytest.raises(ToolValidationError):
        await execute_tool(session, AI, "incident.get", {"incident_id": fx.incident_id, "x": 1})
    with pytest.raises(ToolValidationError):  # sınıf listesi dışı grup
        await execute_tool(
            session, AGENT, "incident.assign",
            {"incident_id": fx.incident_id, "assignment_group": "uydurma"},
        )  # fmt: skip
    assert await session.scalar(select(func.count()).select_from(AuditLog)) == before


# --- R1 ------------------------------------------------------------------------------------


async def test_r1_human_runs_directly(session):
    fx = await seed(session)
    r = await execute_tool(
        session, AGENT, "incident.assign",
        {"incident_id": fx.incident_id, "assignment_group": "network_ops", "assignee": "mehmet"},
    )  # fmt: skip
    assert r.status == ToolStatus.EXECUTED
    inc = await session.get(Incident, fx.incident_id)
    assert inc.assignment_group == "network_ops" and inc.status == IncidentStatus.ASSIGNED
    # İnsan çağrısı için yapay zekâ kararı yazılmaz, denetim yazılır
    assert await session.scalar(select(func.count()).select_from(AIDecision)) == 0
    assert (
        await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "tool.execute")
        )
        == 1
    )


async def test_r1_ai_auto_applies_only_when_policy_allows(session):
    fx = await seed(session)
    args = {"incident_id": fx.incident_id, "assignment_group": "network_ops"}
    r = await execute_tool(session, AI, "incident.assign", args, confidence=0.9, settings=LIVE)
    assert r.status == ToolStatus.EXECUTED
    decision = await session.get(AIDecision, r.decision_id)
    assert decision.outcome == DecisionOutcome.AUTO_APPLIED
    assert (decision.agent, decision.action) == ("incident", "assign")
    assert decision.output["tool"] == "incident.assign" and decision.output["applied"] is True
    assert (await session.get(Incident, fx.incident_id)).assignment_group == "network_ops"


@pytest.mark.parametrize(
    ("settings", "confidence", "outcome"),
    [
        (SHADOW, 0.99, DecisionOutcome.SUGGESTED),  # gölge mod
        (LIVE, 0.2, DecisionOutcome.ESCALATED),  # düşük güven
        (LIVE, None, DecisionOutcome.ESCALATED),  # güven bildirilmedi
    ],
)
async def test_r1_ai_not_auto_creates_action_request(session, settings, confidence, outcome):
    fx = await seed(session)
    args = {"incident_id": fx.incident_id, "assignment_group": "network_ops"}
    r = await execute_tool(
        session, AI, "incident.assign", args, confidence=confidence, settings=settings
    )
    assert r.status == ToolStatus.PENDING_APPROVAL
    assert (await session.get(Incident, fx.incident_id)).assignment_group is None
    ar = await session.get(ActionRequest, r.action_request_id)
    assert ar.state == "pending" and ar.risk_class == RiskClass.R1 and ar.required_approvals == 1
    assert ar.requested_by == "ai:copilot" and ar.incident_id == fx.incident_id
    decision = await session.get(AIDecision, r.decision_id)
    assert decision.outcome == outcome and decision.action_request_id == ar.id


async def test_r1_conditional_risk_raises_to_r2(session):
    fx = await seed(session)
    # P5 -> P1 yükseltmesi: R2 (insan onayı), politika ve güven ne olursa olsun
    args = {"incident_id": fx.incident_id, "impact": "high", "urgency": "high"}
    r = await execute_tool(
        session, AI, "incident.set_priority", args, confidence=1.0, settings=LIVE
    )
    assert r.status == ToolStatus.PENDING_APPROVAL and r.risk_class == RiskClass.R2
    assert (await session.get(Incident, fx.incident_id)).priority == Priority.P5
    # P5 -> P4 (P3..P5 içinde yükseltme) R1 kalır
    args = {"incident_id": fx.incident_id, "impact": "low", "urgency": "medium"}
    r = await execute_tool(
        session, AI, "incident.set_priority", args, confidence=1.0, settings=LIVE
    )
    assert r.status == ToolStatus.EXECUTED and r.risk_class == RiskClass.R1
    assert (await session.get(Incident, fx.incident_id)).priority == Priority.P4


async def test_risk_fn_cannot_lower_declared_class(session, monkeypatch):
    calls: list = []
    spec = make_test_tool("test.lower", RiskClass.R2, calls, risk_fn=lambda ctx, a: RiskClass.R0)
    with registered(monkeypatch, spec):
        r = await execute_tool(session, AGENT, "test.lower", {})
    assert r.status == ToolStatus.PENDING_APPROVAL and r.risk_class == RiskClass.R2
    assert calls == []


async def test_r1_policy_rejected_creates_nothing(session, monkeypatch):
    fx = await seed(session)
    monkeypatch.delitem(policy.POLICIES, ("incident", "assign"))
    args = {"incident_id": fx.incident_id, "assignment_group": "network_ops"}
    r = await execute_tool(session, AI, "incident.assign", args, confidence=1.0, settings=LIVE)
    assert r.status == ToolStatus.REJECTED
    assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 0
    assert (await session.get(AIDecision, r.decision_id)).outcome == DecisionOutcome.REJECTED


# --- R2/R3 ---------------------------------------------------------------------------------


async def test_r2_creates_action_request_for_humans_too(session, monkeypatch):
    fx = await seed(session)
    calls: list = []
    spy_handler(monkeypatch, "incident.resolve", calls)
    r = await execute_tool(
        session, AGENT, "incident.resolve",
        {"incident_id": fx.incident_id, "resolution": "Yeniden başlatıldı"}, reason="Kullanıcı onayladı",
    )  # fmt: skip
    assert r.status == ToolStatus.PENDING_APPROVAL and calls == []
    ar = await session.get(ActionRequest, r.action_request_id)
    assert (
        ar.requested_by == "ayse" and ar.reason == "Kullanıcı onayladı" and ar.decision_id is None
    )
    assert r.to_dict() == {
        "status": "pending_approval",
        "action_request_id": ar.id,
        "tool": "incident.resolve",
        "risk_class": "R2",
        "message": r.message,
    }


async def test_dry_run_reports_outcome_without_writes(session):
    fx = await seed(session)
    args = {"incident_id": fx.incident_id, "resolution": "x"}
    r = await execute_tool(session, AI, "incident.resolve", args, dry_run=True, settings=LIVE)
    assert r.status == ToolStatus.DRY_RUN
    assert r.result["would"] == "pending_approval" and r.result["risk_class"] == "R2"
    r = await execute_tool(
        session, AI, "incident.assign",
        {"incident_id": fx.incident_id, "assignee": "x"}, dry_run=True, confidence=0.9, settings=LIVE,
    )  # fmt: skip
    assert r.result["would"] == "execute"
    assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 0
    assert await session.scalar(select(func.count()).select_from(AIDecision)) == 0
    assert (await session.get(Incident, fx.incident_id)).assignee is None


async def test_required_connector_missing_is_unavailable(session, monkeypatch):
    calls: list = []
    # Gerçek identity.add_to_group artık kayıtlı: test aracı ayrı adla
    spec = make_test_tool("test.needs_identity", RiskClass.R1, calls, connectors=("identity",))
    with registered(monkeypatch, spec), pytest.raises(ToolUnavailableError):
        await execute_tool(session, AGENT, "test.needs_identity", {})
    assert calls == []


async def test_real_identity_tool_without_connector_is_unavailable(session):
    from serviszeka.tools.connectors import connectors

    saved = connectors.snapshot()
    connectors.restore({})
    try:
        with pytest.raises(ToolUnavailableError):
            await execute_tool(
                session, AGENT, "identity.add_to_group", {"user": "ali", "group": "sg-x"}
            )
    finally:
        connectors.restore(saved)


# --- idempotency ---------------------------------------------------------------------------


async def test_idempotency_same_args_replays_previous_result(session):
    fx = await seed(session)
    args = {"incident_id": fx.incident_id, "resolution": "Çözüldü"}
    first = await execute_tool(session, AI, "incident.resolve", args, idempotency_key="k-1")
    again = await execute_tool(session, AI, "incident.resolve", args, idempotency_key="k-1")
    assert again.replayed and again.action_request_id == first.action_request_id
    assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 1
    with pytest.raises(IdempotencyConflictError):
        await execute_tool(
            session, AI, "incident.resolve", {**args, "resolution": "Başka"}, idempotency_key="k-1"
        )
    # Anahtar aktöre özeldir: başka aktör aynı anahtarla yeni istek açar
    other = await execute_tool(
        session, AGENT, "kb.publish", {"article_id": fx.draft_article_id}, idempotency_key="k-1"
    )
    assert other.status == ToolStatus.PENDING_APPROVAL and not other.replayed
    assert await session.scalar(select(func.count()).select_from(ToolInvocation)) == 2


async def test_idempotency_r1_executed_once(session):
    fx = await seed(session)
    args = {"title": "Yazıcı çalışmıyor", "caller": "ali", "description": "3. kat"}
    a = await execute_tool(session, AGENT, "incident.create", args, idempotency_key="abc")
    b = await execute_tool(session, AGENT, "incident.create", args, idempotency_key="abc")
    assert a.result["id"] == b.result["id"] and b.replayed
    assert await session.scalar(select(func.count()).select_from(Incident)) == 2  # seed + 1
    del fx


# --- her işleyici gerçek servisle çalışır (duman testi) -------------------------------------------

CM_APPROVER = CurrentUser("mudur.onay", frozenset({"approver", "change_manager"}))


def _risk_llm():
    from serviszeka.llm import FakeLLM

    return FakeLLM(
        [
            {
                "narrative": "Risk anlatısı",
                "checklist": ["Yedek al"],
                "adjustment": 5,
                "justification": "Bağımlılık var",
                "confidence": 0.9,
            }
        ]
    )


def _direct_cases(fx):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    return {
        "incident.search": (AGENT, {"q": "VPN"}),
        "incident.get": (AGENT, {"incident_id": fx.incident_id}),
        "incident.create": (AGENT, {"title": "Yazıcı", "caller": "ali", "category": None}),
        "incident.classify": (AGENT, {"incident_id": fx.incident_id, "service": "vpn"}),
        "incident.set_priority": (
            AGENT,
            {"incident_id": fx.incident_id, "impact": "medium", "urgency": "low"},
        ),
        "incident.assign": (AGENT, {"incident_id": fx.incident_id, "assignee": "mehmet"}),
        "incident.link_ci": (AGENT, {"incident_id": fx.incident_id, "ci_ids": [fx.ci_id]}),
        "incident.set_on_hold": (
            AGENT,
            {"incident_id": fx.incident_id, "hold_reason": "internal"},
        ),
        "change.search": (AGENT, {"q": "VPN"}),
        "change.get": (AGENT, {"change_id": fx.change_id}),
        "change.calendar": (
            AGENT,
            {"start": now.isoformat(), "end": (now + timedelta(days=7)).isoformat()},
        ),
        "change.check_conflicts": (AGENT, {"change_id": fx.change_id}),
        "change.list_templates": (AGENT, {}),
        "change.draft_rfc": (
            AGENT,
            {"title": "Disk genişletme", "change_type": "normal", "affected_ci_ids": [fx.ci_id]},
        ),
        "change.assess_risk": (AGENT, {"change_id": fx.change_id}),
        "kb.search": (AGENT, {"query": "VPN"}),
        "kb.get": (AGENT, {"article_id": fx.article_id}),
        "kb.draft_article": (AGENT, {"title": "Yeni", "body": "Adımlar"}),
        "kb.update_draft": (AGENT, {"article_id": fx.draft_article_id, "body": "Yeni gövde"}),
        "known_error.create": (
            AGENT,
            {"title": "VPN kopuyor", "body": "MTU", "workaround": "MTU 1400"},
        ),
        "cmdb.search_ci": (AGENT, {"q": "vpn"}),
        "cmdb.get_ci": (AGENT, {"ci_id": fx.ci_id}),
        "cmdb.impact_analysis": (AGENT, {"ci_ids": [fx.ci_id]}),
        "sla.get_status": (AGENT, {"incident_id": fx.incident_id}),
        "sla.list_at_risk": (AGENT, {}),
        "notify.oncall_page": (
            AGENT,
            {"message": "Ağ kesintisi, ali@ornek.com", "incident_id": fx.incident_id},
        ),
        "audit.verify_chain": (ADMIN, {}),
        "audit.search": (ADMIN, {"action": "incident.create"}),
        # Sorun yönetimi (fx.problem_id: _with_problem)
        "problem.search": (AGENT, {"q": "VPN"}),
        "problem.detect_trends": (AGENT, {"window_days": 30}),
        "problem.create": (AGENT, {"title": "Yazıcı sorunları", "incident_ids": [fx.incident_id]}),
        "problem.link_incidents": (
            AGENT,
            {"problem_id": fx.problem_id, "incident_ids": [fx.incident_id]},
        ),
        "problem.record_rca": (
            AGENT,
            {
                "problem_id": fx.problem_id,
                "rca": {
                    "method": "5_why",
                    "steps": [{"question": "Neden?", "answer": "MTU uyumsuz"}],
                    "root_cause": "MTU uyumsuzluğu",
                },
            },
        ),
        "problem.propose_fix": (AGENT, {"problem_id": fx.problem_id}),
    }


async def _with_problem(session, fx):
    """Sorun yönetimi araç örnekleri için bir sorun kaydı (geçici çözümlü) ekler."""
    from dataclasses import replace

    from serviszeka.services import problems
    from serviszeka.services.problem_schemas import ProblemCreate

    problem = await problems.create_problem(
        session,
        ProblemCreate(title="VPN kopmaları", workaround="VPN istemcisini yeniden başlatın"),
        AGENT,
    )
    return replace(fx, problem_id=problem.id)


async def test_every_r0_r1_handler_runs_against_services(session, directory):
    from serviszeka import notifications

    fx = await _with_problem(session, await seed(session))
    cases = {**_direct_cases(fx), **await _connector_cases(session)}
    direct = {n for n in registry.names() if registry.get(n).risk_class in (RiskClass.R0, RiskClass.R1)}
    assert set(cases) == direct
    for name, (actor, args) in cases.items():
        r = await execute_tool(session, actor, name, args, llm=_risk_llm(), settings=TOOL_SETTINGS)
        assert r.status == ToolStatus.EXECUTED, (name, r.error)
        assert isinstance(r.result, dict), name
    # Dış sistem (sahte dizin) yazmaları; geçici parola yalnızca ikincil adrese gitti
    ops = [w[0] for w in directory.writes]
    for op in ("reset_password", "unlock_account", "add_to_group", "assign_license",
               "send_to_user", "post_to_channel"):
        assert op in ops, op
    secret = [m for m in directory.messages if m.sensitive]
    assert len(secret) == 1 and secret[0].target == "ali@kisisel.ornek"
    assert [m.text for m in directory.messages if m.kind == "channel"] == ["Duyuru [EPOSTA]"]
    page = [p for p in notifications.pending(session) if p.event == "oncall.page"]
    assert page and page[0].payload["message"] == "Ağ kesintisi, [EPOSTA]"
    inc = await session.get(Incident, fx.incident_id)
    assert inc.status == IncidentStatus.ON_HOLD and inc.service == "vpn" and inc.ci_ids == [fx.ci_id]


TOOL_SETTINGS = Settings(
    ai_shadow_mode=True, identity_standard_groups=["sg-yazilim-standart", "sg-vpn-kullanicilari"]
)


async def _connector_cases(session):
    """Hizmet talebi + kimlik/lisans/bildirim araçları için örnek çağrılar (sahte dizin)."""
    from serviszeka.services import catalog, requests

    await catalog.seed_default_items(session)
    ali = CurrentUser("ali", frozenset({"requester"}))
    # Self-servis şifre sıfırlama: gölge modda parola adımı onay bekler (talep karşılanıyor)
    reset = await requests.create_request(
        session, await catalog.get_item(session, "password_reset"), requester="ali",
        opened_by=ali, form_values={}, settings=TOOL_SETTINGS,
    )
    assert reset.state == "fulfilling" and reset.waiting_action_id
    # Self-servis kilit açma: gölge modda kilit adımı onay bekler; talep bağlıyken R1
    unlock = await requests.create_request(
        session, await catalog.get_item(session, "account_unlock"), requester="ali",
        opened_by=ali, form_values={}, settings=TOOL_SETTINGS,
    )
    assert unlock.state == "fulfilling" and unlock.waiting_action_id
    manual_item = await catalog.create_item(
        session,
        catalog.CatalogItemInput(
            key="cihaz_teslimi", name="Cihaz teslimi", category="Donanım",
            fulfilment_plan=[{"manual": True, "title": "Cihazı teslim et"}],
        ),
        "yonetici",
    )
    manual = await requests.create_request(
        session, manual_item, requester="mehmet", opened_by=AGENT, form_values={},
        settings=TOOL_SETTINGS,
    )
    await session.commit()
    return {
        "catalog.list_items": (AGENT, {"standard": True}),
        "catalog.get_item": (AGENT, {"item": "vpn_access"}),
        "request.create": (
            AGENT,
            {"item": "vpn_access", "requester": "mehmet", "form_values": {"justification": "Saha"}},
        ),
        "request.get": (AGENT, {"request_id": reset.id}),
        "request.run_fulfillment_step": (AGENT, {"request_id": reset.id}),
        "request.complete": (AGENT, {"request_id": manual.id, "note": "Teslim edildi"}),
        "identity.get_user": (AGENT, {"user": "ali"}),
        "identity.verify_requester": (AGENT, {"request_id": reset.id, "user": "ali"}),
        "identity.reset_password": (AGENT, {"user": "ali", "request_id": reset.id}),
        "identity.unlock_account": (AGENT, {"user": "ali", "request_id": unlock.id}),
        "identity.add_to_group": (AGENT, {"user": "ali", "group": "sg-yazilim-standart"}),
        "license.check_available": (AGENT, {"sku": "SPE_E3"}),
        "license.assign": (AGENT, {"user": "ali", "sku": "spe_e3"}),
        "notify.send_to_user": (AGENT, {"user": "ali", "subject": "Bilgi", "message": "Merhaba"}),
        "notify.post_to_channel": (
            AGENT, {"channel": "servis-masasi", "message": "Duyuru ali@ornek.com"}
        ),
    }


async def _change_in_review(session, change_id):
    from datetime import UTC, datetime, timedelta

    from serviszeka.services import changes

    now = datetime.now(UTC)
    await changes.update_change(
        session,
        change_id,
        changes.ChangeUpdate(
            planned_start=now - timedelta(minutes=5), planned_end=now + timedelta(hours=1)
        ),
        AGENT,
    )
    mgr = CurrentUser("mudur2", frozenset({"change_manager"}))
    await changes.transition(session, change_id, "assess", AGENT)
    await changes.transition(session, change_id, "authorize", mgr)
    for name in ("kurul1", "kurul2"):
        await changes.record_approval(
            session, change_id, CurrentUser(name, frozenset({"cab"})), "approve"
        )
    await changes.transition(session, change_id, "scheduled", mgr)
    await changes.transition(session, change_id, "implement", mgr)
    await changes.transition(session, change_id, "review", mgr)


async def test_every_r2_handler_runs_after_human_approval(session, directory):
    from datetime import UTC, datetime, timedelta

    from serviszeka.models import ChangeRequest, KnowledgeArticle
    from serviszeka.services import changes
    from serviszeka.tools import actions

    fx = await _with_problem(session, await seed(session))
    # İkinci değişiklik: CAB'a gönderilecek (pencere ve planlar tam)
    now = datetime.now(UTC)
    submit = await changes.create_change(
        session,
        changes.ChangeCreate(
            title="Sertifika yenileme",
            change_type="normal",
            implementation_plan="Yenile",
            backout_plan="Eskisine dön",
            planned_start=now + timedelta(days=1),
            planned_end=now + timedelta(days=1, hours=1),
        ),
        AGENT,
    )
    await _change_in_review(session, fx.change_id)
    cases = {
        "incident.resolve": {"incident_id": fx.incident_id, "resolution": "Çözüldü"},
        "change.submit_for_cab": {"change_id": submit.id},
        "change.record_pir": {"change_id": fx.change_id, "review_notes": "PIR tamam"},
        "kb.publish": {"article_id": fx.draft_article_id},
        "kb.retire": {"article_id": fx.article_id},
        "known_error.publish_workaround": {"problem_id": fx.problem_id},
        "problem.close": {"problem_id": fx.problem_id, "resolution": "Kalıcı çözüm doğrulandı"},
    }
    cases.update(
        {
            "identity.reset_mfa": {"user": "ali"},
            "identity.remove_from_group": {"user": "ali", "group": "sg-vpn-kullanicilari"},
            "identity.revoke_sessions": {"user": "ali"},
            "mailbox.grant_shared_access": {"mailbox": "muhasebe", "user": "ali"},
        }
    )
    risky = {n for n in registry.names() if registry.get(n).risk_class == RiskClass.R2}
    assert set(cases) == risky
    for name, args in cases.items():
        r = await execute_tool(session, AI, name, args, confidence=1.0, settings=LIVE)
        assert r.status == ToolStatus.PENDING_APPROVAL, name
        ar = await actions.approve(session, r.action_request_id, CM_APPROVER, settings=LIVE)
        assert ar.state == "executed", (name, ar.error)
    assert (await session.get(ChangeRequest, submit.id)).state == "authorize"
    assert (await session.get(ChangeRequest, fx.change_id)).state == "closed"
    assert (await session.get(KnowledgeArticle, fx.article_id)).state == "retired"
    from serviszeka.models import Problem

    problem = await session.get(Problem, fx.problem_id)
    assert problem.state == "closed" and problem.known_error_article_id


def test_change_schemas_accept_null_assignee():
    """Gerileme: `assignee: null` (temizleme) 500 yerine geçerli olmalı (araç da aynı şemayı kullanır)."""
    from serviszeka.services.change_schemas import ChangeCreate, ChangeUpdate

    assert ChangeCreate(title="x", change_type="normal", assignee=None).assignee is None
    assert ChangeUpdate(assignee=None).model_dump(exclude_unset=True) == {"assignee": None}
    assert ChangeCreate(title="x", change_type="normal", assignee="  mehmet ").assignee == "mehmet"
