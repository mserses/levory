"""Faz 2 kapısı: "Hiçbir riskli eylem onaysız çalışmıyor".

Üç katmanlı kanıt (README "Faz 2 kapısı" bölümü):

1. Politika tablosu: `policy.POLICIES` içindeki her (ajan, eylem) × her risk seviyesi × güven ızgarası
   × eşik × gölge mod açık/kapalı için HIGH/CRITICAL ve "asla otomatik" eylemler hiçbir zaman
   AUTO_APPLIED olmaz.
2. Statik tarama: `src/serviszeka/agents/*.py` modüllerinin AST'si; veritabanına yazan her ajan
   modülü `policy.evaluate` çağırır, `AIDecision` kurar, `audit.record` çağırır ve bir alan
   uygulamadan önce aynı fonksiyonda `policy.evaluate` sonucuna (AUTO_APPLIED) bakar. Ajanlar
   hiçbir zaman durum geçişi/onay fonksiyonu çağırmaz ve `anthropic` SDK'sını doğrudan içe aktarmaz.
3. Davranış: API yapay zekâ aktörüyle ya da onaysız sürülür; uygulama (implement) aşamasına
   gerekli onaylar olmadan ulaşılamaz, yüksek riskli standart değişiklik CAB'ı atlayamaz, acil
   değişiklik PIR olmadan kapanamaz, triage P1/P2'yi onaysız uygulayamaz.
"""

from __future__ import annotations

import ast
import itertools
import math
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from _auth import AGENT, APPROVER, dev_headers
from sqlalchemy import func, select

from serviszeka import auth, db
from serviszeka.api import changes as changes_api
from serviszeka.api import incidents as incidents_api
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import (
    ChangeState,
    CIClass,
    CIEnvironment,
    DecisionOutcome,
    RiskLevel,
)
from serviszeka.llm import FakeLLM
from serviszeka.main import app
from serviszeka.models import ChangeApproval, ChangeRequest
from serviszeka.policy import POLICIES, evaluate
from serviszeka.services import cmdb
from serviszeka.services.cmdb_schemas import CICreate

# --- 1) politika tablosu ----------------------------------------------------------------------

_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
RISKY = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})
# Bu eylemler politikada "asla otomatik" kalmalıdır (gevşetilirse kapı kırılır)
MUST_NEVER_AUTO = frozenset(
    {("incident", "resolve"), ("change", "execute"), ("change", "approve")}
)
# Değişiklik alanındaki yapay zekâ eylemleri en fazla LOW riskte otomatik olabilir
CHANGE_AGENTS = frozenset({"change", "change_risk"})

CONFIDENCES = (0.0, 0.5, 0.7499, 0.75, 0.9, 1.0, -0.1, 1.1, math.nan, math.inf, -math.inf)
THRESHOLDS = (0.0, 0.75, 1.0)
SHADOW = (True, False)


def _grid():
    for (agent, action), risk, conf, thr, shadow in itertools.product(
        sorted(POLICIES), list(RiskLevel), CONFIDENCES, THRESHOLDS, SHADOW
    ):
        yield agent, action, risk, conf, thr, shadow


def test_gate_grid_covers_every_policy_and_risk():
    combos = list(_grid())
    assert {(a, b) for a, b, *_ in combos} == set(POLICIES)
    assert {r for _, _, r, *_ in combos} == set(RiskLevel)
    assert len(combos) == len(POLICIES) * 4 * len(CONFIDENCES) * len(THRESHOLDS) * 2


@pytest.mark.parametrize(("agent", "action"), sorted(POLICIES))
def test_gate_policy_grid_never_auto_applies_risky(agent, action):
    policy = POLICIES[(agent, action)]
    for risk, conf, thr, shadow in itertools.product(
        list(RiskLevel), CONFIDENCES, THRESHOLDS, SHADOW
    ):
        settings = Settings(ai_shadow_mode=shadow, ai_confidence_threshold=thr)
        d = evaluate(agent=agent, action=action, confidence=conf, risk=risk, settings=settings)
        ctx = f"{agent}.{action} risk={risk} conf={conf} thr={thr} shadow={shadow}: {d}"
        if risk in RISKY:
            assert d.outcome in (DecisionOutcome.NEEDS_APPROVAL, DecisionOutcome.ESCALATED), ctx
        if policy.max_auto_risk is None:
            assert d.outcome != DecisionOutcome.AUTO_APPLIED, ctx
        if d.outcome == DecisionOutcome.AUTO_APPLIED:
            # Otomatik uygulama yalnızca: gölge mod kapalı, geçerli güven >= eşik, risk <= sınır
            assert not shadow, ctx
            assert math.isfinite(conf) and thr <= conf <= 1.0, ctx
            assert _RANK[risk] <= _RANK[policy.max_auto_risk], ctx


def test_gate_policy_table_caps():
    for key, policy in POLICIES.items():
        assert policy.max_auto_risk not in RISKY, key  # hiçbir eylem riskli seviyede otomatik değil
        if key[0] in CHANGE_AGENTS and policy.max_auto_risk is not None:
            assert policy.max_auto_risk == RiskLevel.LOW, key
    for key in MUST_NEVER_AUTO:
        assert key in POLICIES and POLICIES[key].max_auto_risk is None, key


def test_gate_unknown_action_rejected():
    for shadow in SHADOW:
        d = evaluate(
            agent="change",
            action="transition",
            confidence=1.0,
            settings=Settings(ai_shadow_mode=shadow, ai_confidence_threshold=0.0),
        )
        assert d.outcome == DecisionOutcome.REJECTED


# --- 2) statik tarama ----------------------------------------------------------------------------
#
# Sezgisel kural (bilinçli olarak basit; kod incelemesinin yerine geçmez):
# - "Yazan modül": `session.add/add_all/delete/merge/execute/flush/commit` çağrısı ya da bir
#   "uygulama" çağrısı içeren modül. Uygulama çağrısı: son adı `apply_`, `update_`, `set_` veya
#   `_set_` ile başlayan çağrı (ör. `incident_service.apply_changes`, `changes._set_risk`).
# - Yazan modül `policy.evaluate(...)` çağırmalı, `AIDecision(...)` kurmalı, `audit.record(...)`
#   çağırmalıdır.
# - Uygulama çağrısı içeren her fonksiyonda, ilk uygulama çağrısından ÖNCE (satır sırası) bir
#   `policy.evaluate` çağrısı bulunmalı ve fonksiyon `AUTO_APPLIED`'a başvurmalıdır. Tek istisna
#   insan onayı yoludur: `approver` / `user` parametresi alan ve `approved_by` yazan fonksiyon
#   (ör. `service_desk.approve_decision`) — uygulamayı insan kararı tetikler.
# - Ajan modülleri hiçbir zaman `transition`, `record_approval`, `approve_ai_decision` çağırmaz ve
#   `anthropic`'i doğrudan içe aktarmaz (LLM yalnızca `serviszeka.llm`).

AGENTS_DIR = Path(__file__).resolve().parents[1] / "src" / "serviszeka" / "agents"
SESSION_WRITES = frozenset({"add", "add_all", "delete", "merge", "execute", "flush", "commit"})
APPLY_RE = re.compile(r"^(apply_|update_|set_|_set_)")
FORBIDDEN_CALLS = frozenset({"transition", "record_approval", "approve_ai_decision"})
HUMAN_PARAMS = frozenset({"approver", "user"})


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _receiver(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.value.id
    return ""


def _is_session_write(node: ast.Call) -> bool:
    return _receiver(node) == "session" and _call_name(node) in SESSION_WRITES


def _is_evaluate(node: ast.Call) -> bool:
    return _receiver(node) == "policy" and _call_name(node) == "evaluate"


def _is_apply(node: ast.Call) -> bool:
    return bool(APPLY_RE.match(_call_name(node))) and _call_name(node) != "setattr"


@dataclass
class AgentScan:
    name: str
    writes: bool = False
    problems: list[str] = field(default_factory=list)


def _human_approval_fn(fn: ast.AST) -> bool:
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    sets_approved = any(
        isinstance(n, ast.Attribute) and n.attr == "approved_by" and isinstance(n.ctx, ast.Store)
        for n in ast.walk(fn)
    )
    return bool(params & HUMAN_PARAMS) and sets_approved


def scan_agent_source(source: str, name: str = "<kaynak>") -> AgentScan:
    tree = ast.parse(source)
    scan = AgentScan(name)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    scan.writes = any(_is_session_write(c) or _is_apply(c) for c in calls)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            if any(m == "anthropic" or m.startswith("anthropic.") for m in mods):
                scan.problems.append("anthropic doğrudan içe aktarılmış")
    for c in calls:
        if _call_name(c) in FORBIDDEN_CALLS:
            scan.problems.append(f"yasak çağrı: {_call_name(c)} (satır {c.lineno})")
    if not scan.writes:
        return scan

    if not any(_is_evaluate(c) for c in calls):
        scan.problems.append("policy.evaluate çağrılmıyor")
    if not any(_call_name(c) == "AIDecision" for c in calls):
        scan.problems.append("AIDecision kaydı oluşturulmuyor")
    if not any(_receiver(c) == "audit" and _call_name(c) == "record" for c in calls):
        scan.problems.append("audit.record çağrılmıyor")

    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        fn_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        applies = sorted(c.lineno for c in fn_calls if _is_apply(c))
        if not applies or _human_approval_fn(fn):
            continue
        evals = sorted(c.lineno for c in fn_calls if _is_evaluate(c))
        if not evals or evals[0] > applies[0]:
            scan.problems.append(f"{fn.name}: uygulama policy.evaluate'ten önce")
        refs_auto = any(
            isinstance(n, ast.Attribute) and n.attr == "AUTO_APPLIED" for n in ast.walk(fn)
        )
        if not refs_auto:
            scan.problems.append(f"{fn.name}: uygulama AUTO_APPLIED sonucuna bağlı değil")
    return scan


def _agent_modules() -> list[Path]:
    return sorted(p for p in AGENTS_DIR.glob("*.py") if p.name != "__init__.py")


def test_gate_static_scan_finds_agents():
    names = {p.stem for p in _agent_modules()}
    assert {"service_desk", "change_risk"} <= names
    writers = {p.stem for p in _agent_modules() if scan_agent_source(p.read_text()).writes}
    assert {"service_desk", "change_risk"} <= writers  # sezgisel kural boş geçmiyor


@pytest.mark.parametrize("path", _agent_modules(), ids=lambda p: p.stem)
def test_gate_static_agents_evaluate_policy_before_applying(path):
    scan = scan_agent_source(path.read_text(encoding="utf-8"), path.name)
    assert scan.problems == [], f"{path.name}: {scan.problems}"


BAD_SOURCES = {
    "no_policy": """
from serviszeka.models import AIDecision
async def run(session, change):
    change.risk_score = 99
    session.add(AIDecision())
    await audit.record(session)
""",
    "apply_before_evaluate": """
async def run(session, svc, change):
    await svc._set_risk(session, change, 99)
    v = policy.evaluate(agent="a", action="b", confidence=1.0)
    session.add(AIDecision())
    await audit.record(session)
    return v.outcome == DecisionOutcome.AUTO_APPLIED
""",
    "ignores_outcome": """
async def run(session, svc, change):
    policy.evaluate(agent="a", action="b", confidence=1.0)
    session.add(AIDecision())
    await audit.record(session)
    await svc.apply_changes(session, change.id, {})
""",
    "transitions": """
async def run(session, svc, change):
    policy.evaluate(agent="a", action="b", confidence=1.0)
    await svc.transition(session, change.id, "implement")
""",
    "direct_sdk": "import anthropic\n",
}


@pytest.mark.parametrize("name", sorted(BAD_SOURCES))
def test_gate_static_scan_detects_violations(name):
    assert scan_agent_source(BAD_SOURCES[name], name).problems


# --- 3) davranış (API) ----------------------------------------------------------------------------

LIVE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0, cab_min_approvals=2)
H_REQ = AGENT  # ayse, agent (talep eden)
H_IMPL = dev_headers("mehmet", "agent")
H_MGR = dev_headers("mudur", "change_manager")
H_MGR2 = dev_headers("mudur2", "change_manager")
H_CAB1 = dev_headers("kurul1", "cab")
H_CAB2 = dev_headers("kurul2", "cab")
AI_USER = CurrentUser("ai:change", frozenset({"admin", "change_manager", "cab", "agent"}))


def _risk_llm(**kw) -> FakeLLM:
    data = {
        "narrative": "Risk anlatısı",
        "checklist": ["Yedek"],
        "adjustment": -10,
        "justification": "Düşük risk olduğunu düşünüyorum",
        "confidence": 1.0,
    }
    data.update(kw)
    return FakeLLM([data])


def _triage_llm(impact: str, urgency: str) -> FakeLLM:
    return FakeLLM(
        [
            {
                "kind": "incident",
                "category": "ag",
                "service": "vpn",
                "impact": impact,
                "urgency": urgency,
                "assignment_group": "network_ops",
                "suggested_resolution": None,
                "kb_article_ids": [],
                "confidence": 1.0,
                "rationale": "test",
            }
        ]
    )


@pytest.fixture
async def api(session):
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    state = {"risk_llm": _risk_llm(), "triage_llm": _triage_llm("high", "high")}
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[changes_api.get_change_settings] = lambda: LIVE
    app.dependency_overrides[changes_api.get_change_llm] = lambda: state["risk_llm"]
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: LIVE
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: state["triage_llm"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, state, session
    app.dependency_overrides.clear()


def _now_window() -> dict:
    now = datetime.now(UTC)
    return {
        "planned_start": (now - timedelta(minutes=10)).isoformat(),
        "planned_end": (now + timedelta(hours=2)).isoformat(),
    }


async def _create(c, **kw) -> str:
    body = {
        "title": "Yama",
        "change_type": "normal",
        "implementation_plan": "Yamayı uygula",
        "backout_plan": "Anlık görüntüden geri dön ve doğrula",
        "assignee": "mehmet",
        **_now_window(),
        **kw,
    }
    r = await c.post("/changes", json=body, headers=H_REQ)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _tr(c, cid, to, headers=H_MGR, **kw) -> httpx.Response:
    return await c.post(f"/changes/{cid}/transition", json={"to_state": to, **kw}, headers=headers)


async def _state(c, cid) -> str:
    return (await c.get(f"/changes/{cid}", headers=H_REQ)).json()["state"]


async def _approve(c, cid, headers) -> httpx.Response:
    return await c.post(f"/changes/{cid}/approvals", json={"decision": "approve"}, headers=headers)


async def _ci(session, name, ci_class, criticality, env) -> str:
    ci = await cmdb.create_ci(
        session,
        CICreate(name=name, ci_class=ci_class, criticality=criticality, environment=env),
        "mudur",
    )
    await session.commit()
    return ci.id


async def test_gate_api_ai_actor_cannot_transition_or_approve(api):
    c, _, _ = api
    cid = await _create(c)
    app.dependency_overrides[auth.get_current_user] = lambda: AI_USER
    try:
        assert (await _tr(c, cid, "assess", {})).status_code == 403
    finally:
        del app.dependency_overrides[auth.get_current_user]
    assert (await _tr(c, cid, "assess", H_REQ)).status_code == 200
    assert (await _tr(c, cid, "authorize")).status_code == 200
    app.dependency_overrides[auth.get_current_user] = lambda: AI_USER
    try:
        assert (await _approve(c, cid, {})).status_code == 403
        for to in ("scheduled", "implement", "cancelled", "rejected"):
            assert (await _tr(c, cid, to, {})).status_code in (403, 422)
    finally:
        del app.dependency_overrides[auth.get_current_user]
    assert await _state(c, cid) == "authorize"
    r = await c.get(f"/changes/{cid}/approvals", headers=H_REQ)
    assert r.json()["items"] == []


async def test_gate_api_ai_actor_cannot_approve_ai_decision(api):
    c, _, _ = api
    cid = await _create(c)
    await _tr(c, cid, "assess", H_REQ)
    d = (await c.post(f"/changes/{cid}/assess", headers=H_REQ)).json()
    assert d["outcome"] == "needs_approval"  # düşürme önerisi
    app.dependency_overrides[auth.get_current_user] = lambda: AI_USER
    try:
        r = await c.post(f"/changes/decisions/{d['id']}/approve", headers={})
        assert r.status_code == 403
    finally:
        del app.dependency_overrides[auth.get_current_user]


async def test_gate_api_risk_agent_never_moves_state(api, session):
    c, state, _ = api
    ci = await _ci(session, "odeme-db", CIClass.DATABASE, 1, CIEnvironment.PROD)
    cid = await _create(c, affected_ci_ids=[ci])
    for adj in (10, -10, 0):
        state["risk_llm"] = _risk_llm(adjustment=adj)
        for _ in range(2):
            assert (await c.post(f"/changes/{cid}/assess", headers=H_MGR)).status_code == 200
            assert await _state(c, cid) == "draft"
    await _tr(c, cid, "assess", H_REQ)
    assert (await c.post(f"/changes/{cid}/assess", headers=H_REQ)).status_code == 200
    assert await _state(c, cid) == "assess"
    await _tr(c, cid, "authorize")
    assert (await c.post(f"/changes/{cid}/assess", headers=H_REQ)).status_code == 422
    assert await _state(c, cid) == "authorize"
    assert await session.scalar(select(func.count()).select_from(ChangeApproval)) == 0


async def test_gate_api_cannot_reach_implement_without_approvals(api, session):
    c, _, _ = api
    cid = await _create(c)
    assert (await _tr(c, cid, "implement", H_IMPL)).status_code == 422  # taslaktan
    await _tr(c, cid, "assess", H_REQ)
    for to in ("scheduled", "implement"):
        assert (await _tr(c, cid, to)).status_code == 422
    assert (await _tr(c, cid, "authorize")).status_code == 200
    for to in ("scheduled", "implement"):
        assert (await _tr(c, cid, to)).status_code == 422
    assert (await _approve(c, cid, H_CAB1)).status_code == 201  # 1/2
    assert (await _tr(c, cid, "scheduled")).status_code == 422
    # Kuralları atlayan yazma: durum zorla scheduled -> uygulama anında yetki yeniden denetlenir
    row = await session.get(ChangeRequest, cid)
    row.state = ChangeState.SCHEDULED
    await session.commit()
    assert (await _tr(c, cid, "implement", H_IMPL)).status_code == 422
    assert (await _tr(c, cid, "implement", H_MGR)).status_code == 422
    assert await _state(c, cid) == "scheduled"


async def test_gate_api_self_approval_does_not_count(api):
    c, _, _ = api
    cid = await _create(c)
    await _tr(c, cid, "assess", H_REQ)
    await _tr(c, cid, "authorize")
    both = dev_headers("ayse", "agent", "cab")  # talep eden CAB üyesi olsa da
    impl = dev_headers("mehmet", "agent", "cab")  # uygulayıcı da
    assert (await _approve(c, cid, both)).status_code == 403
    assert (await _approve(c, cid, impl)).status_code == 403
    assert (await _approve(c, cid, H_CAB1)).status_code == 201
    assert (await _tr(c, cid, "scheduled")).status_code == 422


async def test_gate_api_full_approval_path_reaches_implement(api):
    """Kontrol: gerekli onaylarla aynı akış uygulamaya ulaşır (testler boşuna geçmiyor)."""
    c, _, _ = api
    cid = await _create(c)
    await _tr(c, cid, "assess", H_REQ)
    await _tr(c, cid, "authorize")
    for h in (H_CAB1, H_CAB2):
        assert (await _approve(c, cid, h)).status_code == 201
    assert (await _tr(c, cid, "scheduled")).status_code == 200
    assert (await _tr(c, cid, "implement", H_IMPL)).status_code == 200


async def test_gate_api_high_risk_standard_cannot_bypass_cab(api, session):
    c, _, _ = api
    tpl = {"name": "Yama", "max_risk": "low", "allowed_ci_classes": ["database", "server"]}
    tpl_id = (await c.post("/changes/templates", json=tpl, headers=H_MGR)).json()["id"]
    ci = await _ci(session, "odeme-db", CIClass.DATABASE, 1, CIEnvironment.PROD)
    cid = await _create(c, change_type="standard", template_id=tpl_id, affected_ci_ids=[ci])
    r = await _tr(c, cid, "assess", H_REQ)
    assert r.status_code == 200
    assert r.json()["risk_level"] in ("high", "critical")
    assert r.json()["change_type"] == "normal"  # standart uygunluğu yitirdi
    assert (await _tr(c, cid, "scheduled", H_REQ)).status_code in (403, 422)
    assert (await _tr(c, cid, "scheduled")).status_code == 422
    assert (await _tr(c, cid, "implement", H_REQ)).status_code == 422
    assert await _state(c, cid) == "assess"


async def test_gate_api_forced_high_risk_standard_cannot_implement(api, session):
    c, _, _ = api
    tpl = {"name": "Yama", "max_risk": "low", "allowed_ci_classes": ["server"]}
    tpl_id = (await c.post("/changes/templates", json=tpl, headers=H_MGR)).json()["id"]
    ci = await _ci(session, "srv", CIClass.SERVER, 4, CIEnvironment.DEV)
    cid = await _create(c, change_type="standard", template_id=tpl_id, affected_ci_ids=[ci])
    assert (await _tr(c, cid, "assess", H_REQ)).status_code == 200
    assert (await _tr(c, cid, "scheduled", H_REQ)).status_code == 200  # uygun: CAB atlanır
    row = await session.get(ChangeRequest, cid)
    row.risk_level = RiskLevel.HIGH  # kuralları atlayan yazma
    row.risk_score = 70
    await session.commit()
    assert (await _tr(c, cid, "implement", H_IMPL)).status_code == 422
    assert await _state(c, cid) == "scheduled"


async def _implemented_emergency(c) -> str:
    cid = await _create(c, change_type="emergency")
    assert (await _tr(c, cid, "assess", H_REQ)).status_code == 200
    assert (await _tr(c, cid, "authorize")).status_code == 200
    assert (await _approve(c, cid, H_CAB1)).status_code == 201  # CAB onayı ECAB yerine geçmez
    assert (await _tr(c, cid, "scheduled")).status_code == 422
    assert (await _approve(c, cid, H_MGR2)).status_code == 201
    assert (await _tr(c, cid, "scheduled")).status_code == 200
    assert (await _tr(c, cid, "implement", H_IMPL)).status_code == 200
    assert (await _tr(c, cid, "review", H_IMPL)).status_code == 200
    return cid


@pytest.mark.parametrize("notes", [None, "", "   "])
async def test_gate_api_emergency_close_without_pir_rejected(api, notes):
    c, _, _ = api
    cid = await _implemented_emergency(c)
    kw = {} if notes is None else {"review_notes": notes}
    assert (await _tr(c, cid, "closed", **kw)).status_code == 422
    assert await _state(c, cid) == "review"
    r = await _tr(c, cid, "closed", review_notes="PIR: kök neden ve öğrenilenler")
    assert r.status_code == 200 and r.json()["state"] == "closed"


async def test_gate_api_failed_change_close_without_pir_rejected(api):
    c, _, _ = api
    cid = await _create(c)
    await _tr(c, cid, "assess", H_REQ)
    await _tr(c, cid, "authorize")
    for h in (H_CAB1, H_CAB2):
        await _approve(c, cid, h)
    await _tr(c, cid, "scheduled")
    await _tr(c, cid, "implement", H_IMPL)
    assert (await _tr(c, cid, "failed", H_IMPL)).status_code == 200
    assert (await _tr(c, cid, "review", H_IMPL)).status_code == 200
    assert (await _tr(c, cid, "closed")).status_code == 422
    assert (await _tr(c, cid, "closed", review_notes="Geri alındı, PIR")).status_code == 200


@pytest.mark.parametrize(("impact", "urgency", "expected"), [("high", "high", "P1"), ("high", "medium", "P2")])
async def test_gate_api_triage_cannot_set_p1_p2_without_approval(api, impact, urgency, expected):
    c, state, _ = api
    state["triage_llm"] = _triage_llm(impact, urgency)
    body = {"title": "VPN bağlanamıyorum", "description": "Evden bağlantı yok", "caller": "ayse"}
    inc = (await c.post("/incidents", json=body, headers=AGENT)).json()
    before = inc["priority"]
    assert before not in ("P1", "P2")
    r = await c.post(f"/incidents/{inc['id']}/triage", headers=AGENT)
    assert r.status_code == 200, r.text
    dec = r.json()
    assert dec["outcome"] == "needs_approval"
    after = (await c.get(f"/incidents/{inc['id']}", headers=AGENT)).json()
    assert after["priority"] == before  # gölge mod kapalı, güven 1.0, eşik 0 olsa bile
    # Agent onaylayamaz; yalnızca onaylayıcı insan uygular
    url = f"/incidents/decisions/{dec['id']}/approve"
    assert (await c.post(url, headers=AGENT)).status_code == 403
    assert (await c.post(url, headers=APPROVER)).status_code == 200
    assert (await c.get(f"/incidents/{inc['id']}", headers=AGENT)).json()["priority"] == expected


# --- 4) Faz 3: araç kayıt defteri + eylem istekleri ---------------------------------------------
#
# Kapı: "R2/R3 hiçbir eylem insan onayı olmadan çalışmaz" (standart taleplerin insansız çözümü
# yalnızca R0/R1 araçlarla ve politika izin verirken olur). Kanıt:
# - Her kayıtlı aracın risk sınıfı var; R1-R3 araçlarının politikası var (R1 en fazla LOW otomatik,
#   R2/R3 asla otomatik değil).
# - Her R2/R3 araç (ve koşullu riskin R2'ye çıktığı her R1 araç) yapay zekâ aktörüyle, en gevşek
#   politikayla (gölge kapalı, eşik 0, güven 1.0) çağrılır: işleyici çalışmaz, yalnızca bekleyen
#   ActionRequest oluşur.
# - İşleyicilere yalnızca `tools/executor.py` ve `tools/actions.py` ulaşır (statik tarama).
# - R3 tek onayla çalışmaz; talep eden kendi isteğini onaylayamaz; süresi dolan istek çalışmaz.

TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "serviszeka"
LOOSE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0)
AI_TOOL_USER = CurrentUser("ai:gate", frozenset({"ai_agent"}))

# Her R2/R3 araç için geçerli örnek argümanlar (yeni riskli araç eklenince buraya da eklenmeli)
RISKY_SAMPLES = {
    # Yapay zekâ olay yazması sürüm taşır (expected_version zorunlu; bkz. VERSIONED_AI_SAMPLES)
    "incident.resolve": lambda fx: {
        "incident_id": fx.incident_id, "resolution": "Çözüldü",
        "expected_version": fx.incident_version,
    },
    "change.submit_for_cab": lambda fx: {"change_id": fx.change_id},
    "change.record_pir": lambda fx: {"change_id": fx.change_id, "review_notes": "PIR notu"},
    "kb.publish": lambda fx: {"article_id": fx.draft_article_id},
    "kb.retire": lambda fx: {"article_id": fx.article_id},
    # Faz 3 sorun yönetimi: kapatma ve geçici çözümün yayımlanması her zaman insan onayı ister
    "problem.close": lambda fx: {"problem_id": fx.problem_id, "resolution": "Yinelenen kayıt"},
    "known_error.publish_workaround": lambda fx: {"problem_id": fx.problem_id},
    # Faz 3 kimlik / posta kutusu (Entra ID / M365): R2 her zaman onay, R3 iki onay
    "identity.reset_mfa": lambda fx: {"user": "ali"},
    "identity.remove_from_group": lambda fx: {"user": "ali", "group": "sg-vpn-kullanicilari"},
    "identity.revoke_sessions": lambda fx: {"user": "ali"},
    "identity.disable_user": lambda fx: {"user": "ali", "reason_code": "offboarding"},
    "mailbox.grant_shared_access": lambda fx: {"mailbox": "muhasebe", "user": "ali"},
    # Faz 4 izleme: uyarı grubunu susturmak her zaman insan onayı ister
    "event.silence": lambda fx: {"group_id": fx.event_group_id, "minutes": 60, "comment": "Bakım"},
}
# Koşullu riskin R2'ye çıktığı R1 araç çağrıları (P1/P2 önceliği, SLA saatini durduran bekleme)
CONDITIONAL_RISKY = {
    "incident.create/p1": (
        "incident.create",
        lambda fx: {"title": "Tüm şirket", "caller": "ali", "impact": "high", "urgency": "high"},
    ),
    "incident.set_priority/p1": (
        "incident.set_priority",
        lambda fx: {"incident_id": fx.incident_id, "impact": "high", "urgency": "high",
                    "expected_version": fx.incident_version},
    ),
    "incident.set_priority/p2": (
        "incident.set_priority",
        lambda fx: {"incident_id": fx.incident_id, "impact": "high", "urgency": "medium",
                    "expected_version": fx.incident_version},
    ),
    "incident.set_on_hold/awaiting_caller": (
        "incident.set_on_hold",
        lambda fx: {"incident_id": fx.incident_id, "hold_reason": "awaiting_caller",
                    "expected_version": fx.incident_version},
    ),
    # Faz 4 izleme: kritiklik 1 CI'da kritik uyarı grubu P1 olay açar -> R2
    "event.create_incident/p1": (
        "event.create_incident",
        lambda fx: {"group_id": fx.event_group_id},
    ),
}


def _risk_level(risk_class):
    from serviszeka.domain.enums import RiskClass

    return {RiskClass.R1: RiskLevel.LOW}.get(risk_class)


def test_gate_tools_every_tool_has_risk_class_and_policy():
    from serviszeka.domain.enums import RiskClass
    from serviszeka.tools import registry

    names = registry.names()
    assert len(names) >= 33
    for name in names:
        spec = registry.get(name)
        assert isinstance(spec.risk_class, RiskClass), name
        if spec.risk_class == RiskClass.R0:
            assert not spec.risk_fn, f"{name}: R0 araç koşullu riske sahip olamaz"
            continue
        pol = POLICIES.get((spec.domain, spec.verb))
        assert pol is not None, f"{name}: politika yok"
        # R1 en fazla LOW riskte otomatik; R2/R3 asla otomatik değil
        assert pol.max_auto_risk == _risk_level(spec.risk_class), name


def test_gate_tools_every_risky_tool_has_a_sample():
    from serviszeka.domain.enums import RiskClass
    from serviszeka.tools import registry

    risky = {n for n in registry.names() if registry.get(n).risk_class in (RiskClass.R2, RiskClass.R3)}
    assert risky == set(RISKY_SAMPLES)


def test_gate_tools_handlers_only_reached_through_pipeline():
    allowed = {TOOLS_DIR / "tools" / "executor.py", TOOLS_DIR / "tools" / "actions.py"}
    offenders = []
    for path in TOOLS_DIR.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "handler" and isinstance(
                node.ctx, ast.Load
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def _admin_only(spec) -> bool:
    from serviszeka.auth import ROLE_ADMIN

    return not (set(spec.roles) - {ROLE_ADMIN})


def test_gate_tools_ai_never_calls_admin_only_tools():
    # Ürün sahibi kararı: yapay zekâ yalnızca admin olmayan bir insan rolünün de çağırabildiği
    # araçları çağırır (denetim günlüğü okuma/doğrulama gibi admin-yalnız okumalar hariç)
    import dataclasses

    from serviszeka.tools import can_call, registry

    admin_only = {n for n in registry.names() if _admin_only(registry.get(n))}
    assert {"audit.search", "audit.verify_chain"} <= admin_only
    ai_users = (
        AI_TOOL_USER,
        CurrentUser("ai:gate", frozenset({"ai_agent", "admin"})),
        CurrentUser("mcp-istemci", frozenset({"ai_agent", "admin", "agent"})),
    )
    for name in sorted(admin_only):
        spec = registry.get(name)
        assert spec.ai_allowed is False, f"{name}: admin-yalnız araç ai_allowed=False olmalı"
        for user in ai_users:
            assert not can_call(spec, user), (name, user.username)
        # Yapısal kural: bayrak unutulsa da admin-yalnız araç yapay zekâya kapalıdır
        assert not can_call(dataclasses.replace(spec, ai_allowed=True), AI_TOOL_USER), name
        # İnsan admin çağırabilir
        assert can_call(spec, CurrentUser("admin1", frozenset({"admin"}))), name


@pytest.mark.parametrize("name", ["audit.search", "audit.verify_chain"])
async def test_gate_tools_ai_admin_only_call_is_forbidden_and_writes_nothing(session, name):
    from serviszeka.models import AuditLog, ToolInvocation
    from serviszeka.tools import ToolForbiddenError, execute_tool

    before = await session.scalar(select(func.count()).select_from(AuditLog))
    for user in (AI_TOOL_USER, CurrentUser("ai:gate", frozenset({"ai_agent", "admin"}))):
        with pytest.raises(ToolForbiddenError):
            await execute_tool(session, user, name, {}, confidence=1.0, settings=LOOSE)
    assert await session.scalar(select(func.count()).select_from(AuditLog)) == before
    assert await session.scalar(select(func.count()).select_from(ToolInvocation)) == 0


_GATE_DIRECTORY: dict = {}


@pytest.fixture(autouse=True)
def _gate_fake_connectors():
    """Kimlik/posta/lisans/bildirim araçları için sahte dizin (ağ yok); dış yazma kaydı izlenir."""
    from serviszeka.connectors.memory import InMemoryDirectory
    from serviszeka.connectors.setup import build_fake, install
    from serviszeka.tools.connectors import connectors

    saved = connectors.snapshot()
    _GATE_DIRECTORY["d"] = directory = InMemoryDirectory.demo()
    install(connectors, build_fake(directory))
    try:
        yield directory
    finally:
        connectors.restore(saved)
        _GATE_DIRECTORY.clear()


async def _tool_session_fixtures(session):
    from dataclasses import replace

    from _tooling import AGENT as TOOL_AGENT
    from _tooling import seed

    from serviszeka.services import problems
    from serviszeka.services.problem_schemas import ProblemCreate

    fx = await seed(session)
    problem = await problems.create_problem(
        session,
        ProblemCreate(
            title="VPN kopmaları",
            incident_ids=[fx.incident_id],
            workaround="VPN istemcisini yeniden başlatın",
        ),
        TOOL_AGENT,
    )
    await session.commit()
    from _events import seed_p1_group

    from serviszeka.models import Incident

    version = (await session.get(Incident, fx.incident_id, populate_existing=True)).version
    return replace(
        fx,
        problem_id=problem.id,
        event_group_id=await seed_p1_group(session),
        incident_version=version,
    )


@pytest.mark.parametrize("name", sorted(RISKY_SAMPLES))
async def test_gate_tools_ai_cannot_run_r2_r3_without_approval(session, monkeypatch, name):
    from _tooling import spy_handler

    from serviszeka.models import ActionRequest, AuditLog
    from serviszeka.tools import ToolStatus, execute_tool

    fx = await _tool_session_fixtures(session)
    calls: list = []
    spy_handler(monkeypatch, name, calls)
    r = await execute_tool(
        session, AI_TOOL_USER, name, RISKY_SAMPLES[name](fx), confidence=1.0, settings=LOOSE
    )
    assert r.status == ToolStatus.PENDING_APPROVAL, r
    assert calls == []
    assert _GATE_DIRECTORY["d"].writes == []  # dış sisteme (Entra ID / M365) yazılmadı
    ar = await session.get(ActionRequest, r.action_request_id)
    assert ar.state == "pending" and ar.executed_at is None and ar.result is None
    # Yalnızca istek/karar/denetim yazıldı; alan servisinin denetim eylemi yok
    acts = set((await session.scalars(select(AuditLog.action))).all())
    assert not {
        "incident.update", "change.transition", "knowledge.publish", "knowledge.retire",
        "problem.transition", "problem.known_error.publish",
        "event.group.silence", "event.group.incident", "incident.work_note",
    } & acts
    if fx.problem_id:
        from serviszeka.models import Problem

        problem = await session.get(Problem, fx.problem_id)
        assert problem.state == "new" and problem.known_error_article_id is None


@pytest.mark.parametrize("case", sorted(CONDITIONAL_RISKY))
async def test_gate_tools_conditional_risk_needs_approval(session, case):
    from serviszeka.models import Incident
    from serviszeka.tools import ToolStatus, execute_tool

    fx = await _tool_session_fixtures(session)
    name, sample = CONDITIONAL_RISKY[case]
    before = await session.scalar(select(func.count()).select_from(Incident))
    r = await execute_tool(session, AI_TOOL_USER, name, sample(fx), confidence=1.0, settings=LOOSE)
    assert r.status == ToolStatus.PENDING_APPROVAL and r.risk_class.value == "R2"
    inc = await session.get(Incident, fx.incident_id)
    assert inc.priority.value == "P5" and inc.status.value == "new" and inc.sla_paused_at is None
    assert await session.scalar(select(func.count()).select_from(Incident)) == before


async def test_gate_tools_ai_versioned_write_without_version_writes_nothing(session):
    # Yapay zekâ (ve MCP) olay/değişiklik yazmasında expected_version zorunlu: sürümsüz çağrı
    # en gevşek politikada bile hiçbir şey yazmaz (istek, karar, denetim, kayıt değişikliği yok)
    from serviszeka.models import ActionRequest, AIDecision, AuditLog, Incident
    from serviszeka.tools import ToolValidationError, execute_tool, registry

    fx = await _tool_session_fixtures(session)
    before = (
        await session.scalar(select(func.count()).select_from(AuditLog)),
        (await session.get(Incident, fx.incident_id)).version,
    )
    versioned = [
        n for n in registry.names()
        if n.split(".")[0] in ("incident", "change")
        and "expected_version" in registry.get(n).input_model.model_fields
    ]  # fmt: skip
    assert {"incident.resolve", "incident.assign", "incident.set_priority"} <= set(versioned)
    samples = {
        "incident.resolve": {"incident_id": fx.incident_id, "resolution": "x"},
        "incident.assign": {"incident_id": fx.incident_id, "assignee": "ali"},
        "incident.set_priority": {"incident_id": fx.incident_id, "impact": "high", "urgency": "high"},
        "incident.set_on_hold": {"incident_id": fx.incident_id, "hold_reason": "awaiting_caller"},
        "incident.classify": {"incident_id": fx.incident_id, "service": "vpn"},
        "incident.link_ci": {"incident_id": fx.incident_id, "ci_ids": [fx.ci_id]},
    }
    assert set(versioned) <= set(samples)
    for name in versioned:
        with pytest.raises(ToolValidationError):
            await execute_tool(
                session, AI_TOOL_USER, name, samples[name], confidence=1.0, settings=LOOSE
            )
    assert (await session.scalars(select(ActionRequest))).all() == []
    assert (await session.scalars(select(AIDecision))).all() == []
    after = (
        await session.scalar(select(func.count()).select_from(AuditLog)),
        (await session.get(Incident, fx.incident_id, populate_existing=True)).version,
    )
    assert after == before


async def test_gate_tools_r3_single_approval_insufficient(session, monkeypatch):
    from _tooling import APPROVER, APPROVER2, make_test_tool, registered

    from serviszeka.domain.enums import RiskClass
    from serviszeka.tools import actions, execute_tool

    calls: list = []
    with registered(monkeypatch, make_test_tool("test.gate_critical", RiskClass.R3, calls)):
        r = await execute_tool(session, AI_TOOL_USER, "test.gate_critical", {}, confidence=1.0, settings=LOOSE)
        ar = await actions.approve(session, r.action_request_id, APPROVER)
        assert ar.state == "pending" and calls == []
        # Aynı kişi farklı yazımla ikinci onay veremez
        with pytest.raises(actions.DuplicateActionApprovalError):
            await actions.approve(session, ar.id, CurrentUser(" Sef.Ali", frozenset({"admin"})))
        assert calls == []
        ar = await actions.approve(session, ar.id, APPROVER2)
        assert ar.state == "executed" and len(calls) == 1  # kontrol: iki onayla çalışır


async def test_gate_tools_self_approval_impossible(session, monkeypatch):
    from _tooling import make_test_tool, registered

    from serviszeka.domain.enums import RiskClass
    from serviszeka.tools import actions, execute_tool

    calls: list = []
    requester = CurrentUser("Ayse", frozenset({"agent", "approver"}))
    with registered(monkeypatch, make_test_tool("test.gate_medium", RiskClass.R2, calls)):
        r = await execute_tool(session, requester, "test.gate_medium", {})
        for me in ("ayse", " AYSE ", "Ayse"):
            user = CurrentUser(me, frozenset({"approver", "admin"}))
            with pytest.raises(actions.ActionForbiddenError):
                await actions.approve(session, r.action_request_id, user)
        # Yapay zekâ hiçbir isteği onaylayamaz
        with pytest.raises(actions.ActionForbiddenError):
            await actions.approve(
                session, r.action_request_id, CurrentUser("ai:gate", frozenset({"admin", "approver"}))
            )
    assert calls == []


async def test_gate_tools_expired_request_cannot_execute(session, monkeypatch):
    from _tooling import APPROVER, make_test_tool, registered

    from serviszeka.domain.enums import RiskClass
    from serviszeka.tools import actions, execute_tool

    calls: list = []
    with registered(monkeypatch, make_test_tool("test.gate_medium", RiskClass.R2, calls)):
        r = await execute_tool(session, AI_TOOL_USER, "test.gate_medium", {}, settings=LOOSE)
        later = datetime.now(UTC) + timedelta(hours=25)
        with pytest.raises(actions.ActionExpiredError):
            await actions.approve(session, r.action_request_id, APPROVER, now=later)
        with pytest.raises(actions.ActionStateError):
            await actions.approve(session, r.action_request_id, APPROVER)
    assert calls == []


# --- 5) Faz 3: sorun yönetimi ajanı -----------------------------------------------------------
#
# Sorun ajanı (agents/problem.py) yalnızca araç boru hattından (`execute_tool`, aktör
# `ai:problem`) yazar: sorun önerisi (problem.create R1), RCA taslağı (problem.record_rca R1,
# yalnızca `rca_draft`), kalıcı çözüm için taslak RFC (problem.propose_fix R1). En gevşek
# politikada bile sorunu çözemez/kapatamaz, kesin RCA'yı yazamaz, geçici çözümü yayımlayamaz;
# servis katmanı `ai:` aktörün resolved/closed geçişini ve kesin RCA kaydını reddeder.


async def test_gate_problem_agent_never_resolves_closes_or_publishes(session):
    from _tooling import AGENT as TOOL_AGENT

    from serviszeka.agents import problem as problem_agent
    from serviszeka.domain.enums import IncidentStatus, ProblemState
    from serviszeka.models import ChangeRequest, Incident, KnowledgeArticle, Problem
    from serviszeka.services import incidents as incident_service
    from serviszeka.services import problems
    from serviszeka.services.problem_schemas import ProblemUpdate
    from serviszeka.services.schemas import IncidentCreate

    ci = await cmdb.create_ci(
        session, CICreate(name="vpn-gw", ci_class=CIClass.NETWORK_DEVICE), "mudur"
    )
    for title in ("VPN kopuyor", "VPN yine koptu", "VPN bağlantısı düştü"):
        inc = await incident_service.create_incident(
            session, IncidentCreate(title=title, caller="ali", category="ag"), "ayse"
        )
        await incident_service.set_affected_cis(session, inc.id, [ci.id], "ayse")
    await session.commit()
    results = await problem_agent.propose_problems(session, window_days=30, settings=LOOSE)
    assert results and all(r.status.value == "executed" for r in results)
    llm = FakeLLM([{
        "method": "5_why",
        "steps": [{"question": "Neden?", "answer": "Zaman aşımı"}],
        "root_cause": "Zaman aşımı yanlış",
        "confidence": 1.0,
    }])  # fmt: skip
    pid = (await session.scalars(select(Problem.id))).first()
    await problems.update_problem(
        session, pid, ProblemUpdate(workaround="Yeniden bağlanın"), TOOL_AGENT
    )
    await problem_agent.draft_rca(session, pid, llm, settings=LOOSE)
    await problem_agent.propose_fix(session, pid, settings=LOOSE)
    await session.commit()

    problem = await session.get(Problem, pid)
    assert problem.state == ProblemState.NEW
    assert problem.rca is None and problem.root_cause is None and problem.rca_draft
    assert problem.known_error_article_id is None
    assert await session.scalar(select(func.count()).select_from(KnowledgeArticle)) == 0
    changes_ = (await session.scalars(select(ChangeRequest))).all()
    assert all(c.state == ChangeState.DRAFT for c in changes_)
    statuses = set((await session.scalars(select(Incident.status))).all())
    assert statuses == {IncidentStatus.NEW}
    # Servis savunması: yapay zekâ aktörü sorunu çözemez/kapatamaz, kesin RCA yazamaz
    ai = CurrentUser("ai:problem", frozenset({"ai_agent"}))
    for target in (ProblemState.RESOLVED, ProblemState.CLOSED):
        with pytest.raises(problems.ProblemForbiddenError):
            await problems.transition(session, pid, target, ai, note="not")
    with pytest.raises(problems.ProblemForbiddenError):
        await problems.accept_rca_draft(session, pid, ai)
    with pytest.raises(problems.ProblemForbiddenError):
        await problems.publish_known_error(session, pid, ai)


# --- 6) Faz 3: hizmet talepleri + kimlik bağlayıcıları (Entra ID / M365 / Teams) ---------------
#
# Kapı: "standart taleplerin %50'si insansız çözülüyor" VE bu sırada her R2/R3 adım insan onayı
# ister. Kanıt:
# - Koşullu kimlik riski: izin listesi dışı / ayrıcalıklı / bilinmeyen gruba ekleme R3, doğrulanmamış
#   parola sıfırlama R2; en gevşek politikada bile dış sisteme yazılmaz.
# - Katalog planındaki R2/R3 adım (iş onayı verilmiş olsa da) `ai:fulfilment` ile yalnızca eylem
#   isteği açar; R3 iki farklı insan ister; talep eden / açan / hedef kişi adımı onaylayamaz; yapay
#   zekâ ve talep eden iş onayı veremez; iptal bekleyen isteği geçersiz kılar; gölge modda hiçbir
#   adım otomatik çalışmaz.
# - Gerçekçi standart talep karışımında (request_evaluation.SCENARIO) oran >= %50 ve değişmezler
#   (insansız talepte insan onayı yok; her R2/R3 yürütmesi gerekli insan onayıyla) sağlanır.
# - Karşılama motoru araçları yalnızca yapay zekâ aktörüyle (`ai:fulfilment`) çağırır (statik).

LOOSE_STD = Settings(
    ai_shadow_mode=False,
    ai_confidence_threshold=0.0,
    identity_standard_groups=["sg-yazilim-standart", "sg-vpn-kullanicilari", "m365-ekip-proje-x"],
)

IDENTITY_CONDITIONAL = {
    "add_to_group/unlisted": (
        "identity.add_to_group", {"user": "ali", "group": "sg-finans-paylasim"}, "R3", LOOSE_STD
    ),
    "add_to_group/privileged_on_allow_list": (
        "identity.add_to_group",
        {"user": "ali", "group": "sg-domain-admins"},
        "R3",
        Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0,
                 identity_standard_groups=["sg-domain-admins"]),
    ),
    "add_to_group/configured_privileged": (
        "identity.add_to_group",
        {"user": "ali", "group": "sg-vpn-kullanicilari"},
        "R3",
        Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0,
                 identity_standard_groups=["sg-vpn-kullanicilari"],
                 identity_privileged_groups=["sg-vpn-kullanicilari"]),
    ),
    # Ayrıcalıklı liste harf büyüklüğü / Unicode biçimiyle farklı yazılmış olsa da (Entra grup
    # kimlikleri büyük/küçük harf duyarsızdır) izin listesindeki grup ayrıcalıklı sayılır
    "add_to_group/configured_privileged_case_variant": (
        "identity.add_to_group",
        {"user": "ali", "group": "sg-vpn-kullanicilari"},
        "R3",
        Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0,
                 identity_standard_groups=["sg-vpn-kullanicilari"],
                 identity_privileged_groups=["ＳＧ-VPN-KULLANICILARI "]),
    ),
    "add_to_group/unknown_group": (
        "identity.add_to_group",
        {"user": "ali", "group": "yok-boyle-grup"},
        "R3",
        Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0,
                 identity_standard_groups=["yok-boyle-grup"]),
    ),
    "reset_password/no_request": ("identity.reset_password", {"user": "ali"}, "R2", LOOSE),
    # Kilit açma da parola sıfırlama gibi: doğrulanmış self-servis talep dışında R2
    "unlock_account/no_request": ("identity.unlock_account", {"user": "ali"}, "R2", LOOSE),
    # Ürün sahibi kararı: standart gruba ekleme ve lisans atama da hesaba/erişime dokunur;
    # doğrulanmış self-servis talep dışında (talepsiz / başkası adına / devre dışı hesap) R2
    "add_to_group/standard_no_request": (
        "identity.add_to_group", {"user": "ali", "group": "sg-vpn-kullanicilari"}, "R2", LOOSE_STD
    ),
    "license_assign/no_request": (
        "license.assign", {"user": "ali", "sku": "SPE_E3"}, "R2", LOOSE
    ),
}


@pytest.mark.parametrize("case", sorted(IDENTITY_CONDITIONAL))
async def test_gate_identity_conditional_risk_needs_approval(session, case):
    from serviszeka.tools import ToolStatus, execute_tool

    name, args, expected, settings = IDENTITY_CONDITIONAL[case]
    d = _GATE_DIRECTORY["d"]
    r = await execute_tool(session, AI_TOOL_USER, name, args, confidence=1.0, settings=settings)
    assert r.status == ToolStatus.PENDING_APPROVAL and r.risk_class.value == expected, r
    assert d.writes == [] and d.messages == []


def _req_users():
    return (
        CurrentUser("ali", frozenset({"requester"})),
        CurrentUser("zeynep", frozenset({"requester"})),  # demo dizinde yönetici + İK onaycısı
        CurrentUser("ayse", frozenset({"agent"})),
        CurrentUser("mehmet", frozenset({"requester"})),
    )


async def _open(session, key, requester, opener, form, *, settings=LOOSE_STD, approve=False):
    from serviszeka.services import catalog, requests

    await catalog.seed_default_items(session)
    item = await catalog.get_item(session, key)
    req = await requests.create_request(
        session, item, requester=requester, opened_by=opener, form_values=form, settings=settings
    )
    if approve:
        _, zeynep, _, _ = _req_users()
        req = await requests.decide(session, req.id, zeynep, "approve", settings=settings)
    return req


RISKY_REQUESTS = {
    # (öğe, talep eden, açan, form, iş onayı?, beklenen risk, yasak dış yazma)
    "mfa_reset": ("mfa_reset", "ali", "ali", {}, False, "R2", "reset_mfa"),
    "shared_mailbox": (
        "shared_mailbox_access", "ali", "ali", {"mailbox": "muhasebe"}, True, "R2",
        "grant_shared_access",
    ),
    "privileged_group": (
        "privileged_group_access", "ali", "ali",
        {"group": "sg-domain-admins", "justification": "Sunucu bakımı için yetki"}, True, "R3",
        "add_to_group",
    ),
    "password_reset_on_behalf": ("password_reset", "ali", "ayse", {}, False, "R2", "reset_password"),
    "account_unlock_on_behalf": ("account_unlock", "ali", "ayse", {}, False, "R2", "unlock_account"),
    # Standart grup / lisans: başkası adına açılan talepte R2 (verify_self_service)
    "vpn_access_on_behalf": (
        "vpn_access", "ali", "ayse", {"justification": "Uzaktan çalışma"}, False, "R2",
        "add_to_group",
    ),
    "software_group_on_behalf": (
        "standard_software_group", "ali", "ayse", {"group": "sg-yazilim-standart"}, False, "R2",
        "add_to_group",
    ),
    "m365_license_on_behalf": (
        "m365_license", "ali", "ayse", {"sku": "SPE_E3"}, False, "R2", "assign_license"
    ),
    "new_employee": (
        "new_employee_account", "mehmet", "mehmet",
        {"user": "yeni.calisan", "mailbox": "muhasebe"}, True, "R2", "grant_shared_access",
    ),
}


@pytest.mark.parametrize("case", sorted(RISKY_REQUESTS))
async def test_gate_request_risky_steps_wait_for_human_approval(session, case):
    from serviszeka.models import ActionRequest

    key, requester, opener, form, approve, risk, forbidden = RISKY_REQUESTS[case]
    d = _GATE_DIRECTORY["d"]
    roles = frozenset({"agent"}) if opener == "ayse" else frozenset({"requester"})
    req = await _open(session, key, requester, CurrentUser(opener, roles), form, approve=approve)
    assert req.state == "fulfilling" and req.waiting_action_id, (req.state, req.error)
    ar = await session.get(ActionRequest, req.waiting_action_id)
    assert ar.state == "pending" and ar.requested_by == "ai:fulfilment"
    assert ar.risk_class.value == risk and ar.request_id == req.id
    assert [w for w in d.writes if w[0] == forbidden] == []
    assert req.touchless is None and req.fulfilled_at is None


@pytest.mark.parametrize(
    ("key", "form", "forbidden"),
    [
        ("vpn_access", {"justification": "Uzaktan çalışma"}, "add_to_group"),
        ("standard_software_group", {"group": "sg-yazilim-standart"}, "add_to_group"),
        ("m365_license", {"sku": "SPE_E3"}, "assign_license"),
    ],
)
async def test_gate_request_disabled_account_group_or_license_needs_approval(
    session, key, form, forbidden
):
    # Devre dışı hesap kendi talebini açmış olsa da standart grup / lisans adımı R2 (insan onayı)
    from serviszeka.models import ActionRequest

    d = _GATE_DIRECTORY["d"]
    ali = await d.get_user("ali")
    d.users[ali.id].account_enabled = False
    req = await _open(session, key, "ali", CurrentUser("ali", frozenset({"requester"})), form)
    assert req.state == "fulfilling" and req.waiting_action_id, (req.state, req.error)
    ar = await session.get(ActionRequest, req.waiting_action_id)
    assert ar.state == "pending" and ar.risk_class.value == "R2"
    assert [w for w in d.writes if w[0] == forbidden] == []
    assert req.touchless is None


async def test_gate_request_r3_step_needs_two_distinct_humans(session):
    from serviszeka.tools import actions

    d = _GATE_DIRECTORY["d"]
    ali, *_ = _req_users()
    req = await _open(
        session, "privileged_group_access", "ali", ali,
        {"group": "sg-domain-admins", "justification": "Sunucu bakımı için yetki"}, approve=True,
    )
    ar_id = req.waiting_action_id
    ar = await actions.approve(session, ar_id, CurrentUser("sef.ali", frozenset({"approver"})),
                               settings=LOOSE_STD)
    assert ar.state == "pending" and [w for w in d.writes if w[0] == "add_to_group"] == []
    with pytest.raises(actions.DuplicateActionApprovalError):
        await actions.approve(session, ar_id, CurrentUser("SEF.ALI ", frozenset({"admin"})),
                              settings=LOOSE_STD)
    ar = await actions.approve(session, ar_id, CurrentUser("sef.veli", frozenset({"approver"})),
                               settings=LOOSE_STD)
    assert ar.state == "executed"
    await session.refresh(req)
    assert req.state == "fulfilled" and req.touchless is False
    assert req.human_approvals == 3  # iş onayı + iki eylem onayı
    assert d.writes[0][0] == "add_to_group"


async def test_gate_request_parties_and_ai_cannot_approve(session):
    from serviszeka.services import requests
    from serviszeka.tools import actions

    ali, _, ayse, _ = _req_users()
    # Talep eden / hedef kişi (approver rolü olsa bile) kendi talebinin adımını onaylayamaz
    req = await _open(session, "mfa_reset", "ali", ali, {})
    for who in ("ali", " ALI ", "ali@ornek.com.tr", "u-ali"):
        with pytest.raises(actions.ActionForbiddenError):
            await actions.approve(session, req.waiting_action_id,
                                  CurrentUser(who, frozenset({"approver", "admin"})))
    # Başkası adına açan ajan da onaylayamaz
    req2 = await _open(session, "password_reset", "mehmet", ayse, {})
    with pytest.raises(actions.ActionForbiddenError):
        await actions.approve(session, req2.waiting_action_id,
                              CurrentUser("ayse", frozenset({"approver"})))
    # İş onayı: yapay zekâ, talep eden ve açan veremez
    req3 = await _open(session, "shared_mailbox_access", "ali", ali, {"mailbox": "muhasebe"})
    assert req3.state == "awaiting_approval"
    for user in (
        CurrentUser("ai:gate", frozenset({"admin"})),
        CurrentUser("Ali", frozenset({"admin"})),
    ):
        with pytest.raises(requests.RequestForbiddenError):
            await requests.decide(session, req3.id, user, "approve", settings=LOOSE_STD)
    # Yönetici olmayan sıradan kullanıcı da onaylayamaz
    with pytest.raises(requests.RequestForbiddenError):
        await requests.decide(session, req3.id, CurrentUser("mehmet", frozenset({"approver"})),
                              "approve", settings=LOOSE_STD)
    assert _GATE_DIRECTORY["d"].writes == []


async def test_gate_request_step_target_cannot_give_business_approval(session):
    # Güvenlik incelemesi: planın hedef kullanıcısı (yeni çalışan hesabında `form.user`) onay grubu
    # üyesi olsa da (ya da admin) kendi lehine iş onayı veremez; aksi halde lisans/grup R1 adımları
    # onun hesabına bağımsız bir onay olmadan çalışırdı.
    from serviszeka.services import requests

    d = _GATE_DIRECTORY["d"]
    *_, mehmet = _req_users()
    req = await _open(session, "new_employee_account", "mehmet", mehmet,
                      {"user": "zeynep", "mailbox": "muhasebe"})
    assert req.state == "awaiting_approval"
    for who, roles in (("zeynep", {"requester"}), (" ZEYNEP ", {"approver"}),
                       ("u-zeynep", {"requester"}), ("zeynep", {"admin"})):
        user = CurrentUser(who, frozenset(roles))
        assert await requests.can_decide(user, req) is False, who
        with pytest.raises(requests.RequestForbiddenError):
            await requests.decide(session, req.id, user, "approve", settings=LOOSE_STD)
    assert d.writes == []
    # Kontrol: hedef olmayan başka bir onay grubu üyesi karar verebilir
    d.add_member("sg-ik-onaycilari", "ayse")
    req = await requests.decide(session, req.id, CurrentUser("ayse", frozenset({"requester"})),
                                "approve", settings=LOOSE_STD)
    assert req.approval_state == "approved"


async def test_gate_request_business_approver_cannot_also_approve_action(session):
    # Güvenlik incelemesi: iş onayı ve risk (eylem isteği) onayı ayrı kişilerden gelir. Yönetici
    # approver rolüne de sahipse posta kutusu (R2) ya da yetkili grup (R3) adımını kendisi onaylayıp
    # iki/üç göz ilkesini tek kişiye indiremez.
    from serviszeka.services import requests
    from serviszeka.tools import actions

    d = _GATE_DIRECTORY["d"]
    ali, *_ = _req_users()
    manager = CurrentUser("zeynep", frozenset({"requester", "approver"}))
    req = await _open(session, "shared_mailbox_access", "ali", ali, {"mailbox": "muhasebe"})
    req = await requests.decide(session, req.id, manager, "approve", settings=LOOSE_STD)
    ar_id = req.waiting_action_id
    for who in ("zeynep", "Zeynep ", "u-zeynep"):
        with pytest.raises(actions.ActionForbiddenError):
            await actions.approve(session, ar_id, CurrentUser(who, frozenset({"approver"})),
                                  settings=LOOSE_STD)
    assert [w for w in d.writes if w[0] == "grant_shared_access"] == []
    ar = await actions.approve(session, ar_id, CurrentUser("sef.ali", frozenset({"approver"})),
                               settings=LOOSE_STD)
    assert ar.state == "executed"
    # R3: yönetici + tek BT onayı yetmez (yönetici ikinci onaylayan olamaz)
    req = await _open(session, "privileged_group_access", "ali", ali,
                      {"group": "sg-domain-admins", "justification": "Sunucu bakımı için yetki"})
    req = await requests.decide(session, req.id, manager, "approve", settings=LOOSE_STD)
    await actions.approve(session, req.waiting_action_id,
                          CurrentUser("sef.ali", frozenset({"approver"})), settings=LOOSE_STD)
    with pytest.raises(actions.ActionForbiddenError):
        await actions.approve(session, req.waiting_action_id, manager, settings=LOOSE_STD)
    assert [w for w in d.writes if w[0] == "add_to_group"] == []


async def test_gate_request_form_edit_after_business_approval_is_refused(session):
    # Güvenlik incelemesi: İK, `yeni.calisan` için onay verdi; adım başarısız olunca formu başka
    # bir kullanıcıya çevirip yeniden denemek, onaylanmamış hedefe R1 adımları (lisans, grup)
    # çalıştırırdı. Onaylı talebin formu değiştirilemez; yeniden deneme onaylı değerlerle çalışır.
    from serviszeka.models import ActionRequest
    from serviszeka.services import requests
    from serviszeka.tools import actions

    d = _GATE_DIRECTORY["d"]
    _, zeynep, ayse, mehmet = _req_users()
    d.add_sku("sku-spe-e3", "SPE_E3", enabled=100, consumed=100)  # boş lisans yok -> adım başarısız
    req = await _open(session, "new_employee_account", "mehmet", mehmet,
                      {"user": "yeni.calisan", "mailbox": "muhasebe"})
    req = await requests.decide(session, req.id, zeynep, "approve", settings=LOOSE_STD)
    # Başkası adına lisans atama R2: BT onayıyla yürütülür, boş lisans olmadığı için başarısız
    it = CurrentUser("sef.ali", frozenset({"approver"}))
    ar = await session.get(ActionRequest, req.waiting_action_id)
    assert ar.tool == "license.assign" and ar.risk_class.value == "R2"
    await actions.approve(session, ar.id, it, settings=LOOSE_STD)
    assert req.state == "failed", (req.state, req.error)
    with pytest.raises(requests.RequestStateError):
        await requests.update_form(session, req.id, ayse, {"user": "ali", "mailbox": "muhasebe"})
    assert req.form_values["user"] == "yeni.calisan"
    d.add_sku("sku-spe-e3", "SPE_E3", enabled=100, consumed=10)
    req = await requests.retry(session, req.id, ayse, settings=LOOSE_STD)
    ar = await session.get(ActionRequest, req.waiting_action_id)
    assert ar.tool == "license.assign" and ar.args["user"] == "yeni.calisan"
    await actions.approve(session, ar.id, it, settings=LOOSE_STD)
    assigned = [w[1]["user_id"] for w in d.writes if w[0] == "assign_license"]
    assert assigned == ["u-yeni.calisan"]
    # Onay verilmemiş (iş onayı gerekmeyen) talepte düzeltme serbest kalır (insan dokunuşu)
    std = await _open(session, "standard_software_group", "mehmet", ayse,
                      {"group": "sg-yok-boyle"})
    assert std.approval_state == "not_required"


async def test_gate_request_party_check_fails_closed_when_directory_unavailable(
    session, monkeypatch
):
    # Güvenlik incelemesi: talep edenin dizindeki diğer kimliği (`u-ali`) dizin yanıt vermezken
    # "farklı kişi" sayılıp kendi isteğini onaylayamaz (kimlik denetimi kapalı-başarısız olmalı).
    from serviszeka.connectors.base import ConnectorError
    from serviszeka.services import requests
    from serviszeka.tools import actions

    d = _GATE_DIRECTORY["d"]
    ali, *_ = _req_users()
    req = await _open(session, "mfa_reset", "ali", ali, {})
    ar_id = req.waiting_action_id
    req2 = await _open(session, "shared_mailbox_access", "ali", ali, {"mailbox": "muhasebe"})

    async def down(user):
        raise ConnectorError("dizin yanıt vermiyor")

    monkeypatch.setattr(d, "get_user", down)
    with pytest.raises(actions.ActionForbiddenError):
        await actions.approve(session, ar_id, CurrentUser("u-ali", frozenset({"approver"})))
    alias_admin = CurrentUser("u-ali", frozenset({"admin"}))
    assert await requests.can_decide(alias_admin, req2) is False
    with pytest.raises(requests.RequestForbiddenError):
        await requests.decide(session, req2.id, alias_admin, "approve", settings=LOOSE_STD)
    assert d.writes == []


async def test_gate_request_shadow_mode_runs_no_step(session):
    from serviszeka.models import ActionRequest
    from serviszeka.tools import actions

    d = _GATE_DIRECTORY["d"]
    shadow = Settings(ai_shadow_mode=True, ai_confidence_threshold=0.0,
                      identity_standard_groups=LOOSE_STD.identity_standard_groups)
    ali, *_ = _req_users()
    req = await _open(session, "vpn_access", "ali", ali, {"justification": "Evden"},
                      settings=shadow)
    ar = await session.get(ActionRequest, req.waiting_action_id)
    assert ar.risk_class.value == "R1" and ar.state == "pending" and d.writes == []
    await actions.approve(session, ar.id, CurrentUser("sef.ali", frozenset({"approver"})),
                          settings=shadow)
    await session.refresh(req)
    assert [w[0] for w in d.writes] == ["add_to_group"]
    # Sonraki R1 adım (bildirim) de gölge modda yine onay bekler; talep insansız sayılamaz
    assert req.state == "fulfilling" and req.touchless is None and req.human_approvals == 1


async def test_gate_request_cancel_voids_pending_action(session):
    from serviszeka.services import requests
    from serviszeka.tools import actions

    ali, *_ = _req_users()
    req = await _open(session, "mfa_reset", "ali", ali, {})
    ar_id = req.waiting_action_id
    await requests.cancel(session, req.id, ali, "Telefonu buldum")
    with pytest.raises(actions.ActionStateError):
        await actions.approve(session, ar_id, CurrentUser("sef.ali", frozenset({"approver"})))
    assert _GATE_DIRECTORY["d"].writes == []


async def test_gate_request_catalog_plan_cannot_lower_step_risk(session):
    from serviszeka.models import ActionRequest
    from serviszeka.services import catalog, requests

    with pytest.raises(catalog.CatalogError):
        catalog.validate_plan(
            [{"tool": "request.create", "title": "Kendini çağır", "args": {}}],
            {"type": "object", "properties": {}},
        )
    item = await catalog.create_item(
        session,
        catalog.CatalogItemInput(
            key="ayrilan_calisan",
            name="Ayrılan çalışan",
            category="İşten çıkış",
            standard=True,  # standart işaretlemek adımın riskini düşürmez
            form_schema={"type": "object", "properties": {"user": {"type": "string", "format": "user"}},
                         "required": ["user"]},
            fulfilment_plan=[{"tool": "identity.disable_user", "title": "Devre dışı bırak",
                              "args": {"user": "{{form.user}}"}}],
        ),
        "yonetici",
    )
    req = await requests.create_request(
        session, item, requester="mehmet", opened_by=CurrentUser("mehmet", frozenset({"agent"})),
        form_values={"user": "ali"}, settings=LOOSE_STD,
    )
    ar = await session.get(ActionRequest, req.waiting_action_id)
    assert ar.risk_class.value == "R3" and ar.required_approvals == 2
    assert _GATE_DIRECTORY["d"].writes == []


def test_gate_fulfilment_engine_calls_tools_only_as_ai():
    from serviszeka.services import requests

    assert requests.FULFILMENT_ACTOR.is_ai and requests.FULFILMENT_ACTOR.roles == {"ai_agent"}
    tree = ast.parse((TOOLS_DIR / "services" / "requests.py").read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_name(n) == "execute_tool"
    ]
    assert calls, "karşılama motoru execute_tool çağırmalı"
    for call in calls:
        actor = call.args[1]
        assert isinstance(actor, ast.Name) and actor.id == "FULFILMENT_ACTOR", ast.dump(actor)


async def test_gate_standard_requests_half_touchless_and_risky_steps_approved(session):
    from serviszeka.request_evaluation import gate_settings, run_scenario

    result = await run_scenario(session, gate_settings())
    m = result.metrics
    assert result.violations == []
    assert m.standard_closed >= 20 and m.meets_gate, m.to_dict()
    assert m.standard_touchless_rate >= 0.5
    # Standart olmayan (R2/R3 adımlı) talepler hiçbir zaman insansız sayılmaz
    assert all(not o["touchless"] for o in result.outcomes if not o["standard"])


async def test_gate_shadow_mode_nothing_is_touchless(session):
    from serviszeka.request_evaluation import gate_settings, run_scenario

    # Gölge modda aynı karışımda hiçbir talep insansız değildir (her R1 adım insan onayından geçer)
    result = await run_scenario(session, gate_settings(ai_shadow_mode=True))
    assert result.violations == []
    assert result.metrics.all_touchless == 0 and not result.metrics.meets_gate


async def test_gate_labelled_requests_half_touchless_zero_false_touchless():
    # Faz 3 kapı ölçümü: etiketli 200 talep (evals/data/requests.jsonl) gerçek boru hattında.
    # Otomatikleşmemesi gereken (ayrıcalıklı/bilinmeyen grup, doğrulanmamış talep eden...) hiçbir
    # talep insansız bitmez; her R2/R3 yürütmesi gerekli insan onayıyla; LLM çağrılmaz.
    from serviszeka import automation_eval

    report = await automation_eval.run(automation_eval.load_dataset())
    assert report.false_touchless == [] and report.violations == [] and report.unresolved == []
    assert report.llm_calls == 0
    assert report.standard_touchless_rate >= 0.5 and report.passed


# --- 7) Faz 4: raporlama araçları (KPI / SLA raporu / ihlal tahmini) yalnızca okur ---------------
#
# Kapı: kpi.report, sla.report ve sla.forecast R0'dır, koşullu riski yoktur, politika tablosunda
# girdisi vardır; yapay zekâ en gevşek politikada çağırdığında bile karar/eylem isteği/idempotency
# kaydı ya da kayıt değişikliği oluşmaz (yalnızca `tool.read` denetimi). Hesaplayan servisler
# (services/kpi.py, services/sla_forecast.py) statik olarak yazma/commit/denetim çağrısı içermez.

PHASE4_READ_TOOLS = {
    "kpi.report": {"days": 30, "kpis": ["mttr", "backlog"]},
    "sla.report": {"days": 7},
    "sla.forecast": {"assignment_group": "network_ops", "min_probability": 0.0},
}


def test_gate_phase4_reporting_tools_are_r0_with_policy():
    from serviszeka.domain.enums import RiskClass
    from serviszeka.tools import registry

    for name in PHASE4_READ_TOOLS:
        spec = registry.get(name)
        assert spec.risk_class == RiskClass.R0 and spec.risk_fn is None, name
        assert (spec.domain, spec.verb) in POLICIES, name
        assert POLICIES[(spec.domain, spec.verb)].max_auto_risk in (RiskLevel.LOW, None), name


def test_gate_phase4_services_never_write():
    banned = re.compile(
        r"session\.(add|add_all|flush|commit|delete|merge|refresh)\b"
        r"|(?<![.\w])(insert|update|delete)\("
        r"|audit\.record|notifications\.stage|execute_tool|with_for_update"
    )
    for rel in ("services/kpi.py", "services/sla_forecast.py", "tools/builtin/kpi.py"):
        source = (TOOLS_DIR / rel).read_text(encoding="utf-8")
        assert not banned.search(source), (rel, banned.search(source).group(0))


@pytest.mark.parametrize("name", sorted(PHASE4_READ_TOOLS))
async def test_gate_phase4_ai_reporting_call_writes_nothing(session, name):
    from serviszeka.models import ActionRequest, AIDecision, AuditLog, Incident, ToolInvocation
    from serviszeka.services import incidents as incident_service
    from serviszeka.services.schemas import IncidentCreate
    from serviszeka.tools import ToolStatus, execute_tool

    inc = await incident_service.create_incident(
        session, IncidentCreate(title="VPN kopuyor", caller="ali"), "ayse"
    )
    version = inc.version
    before = await session.scalar(select(func.count()).select_from(AuditLog))
    r = await execute_tool(
        session, AI_TOOL_USER, name, PHASE4_READ_TOOLS[name], idempotency_key="k-1",
        confidence=1.0, settings=LOOSE,
    )  # fmt: skip
    assert r.status == ToolStatus.EXECUTED, r.error
    for model in (AIDecision, ActionRequest, ToolInvocation):
        assert await session.scalar(select(func.count()).select_from(model)) == 0, model
    actions = (await session.scalars(select(AuditLog.action).order_by(AuditLog.seq))).all()
    assert len(actions) == before + 1 and actions[-1] == "tool.read"
    assert (await session.get(Incident, inc.id)).version == version


# --- 8) Faz 4: izleme ve olay yönetimi (event.*, agents/events.py) --------------------------------
#
# Kapı: uyarılardan olay açma yapay zekâ eylemidir ve araç boru hattından (`ai:events`) geçer.
# - event.list_alerts / event.correlate R0 (yazmaz), event.acknowledge R1, event.create_incident R1
#   (P1/P2 olay açacaksa R2), event.silence R2; hepsinin politika girdisi var.
# - En gevşek politikada bile: P1 olacak grup için olay açılmaz (yalnızca bekleyen R2 istek, ikinci
#   işlemde yinelenmez); çözülen uyarılar olayı çözmez (not + bekleyen `incident.resolve` R2 isteği).
# - Ajan modülü veritabanına doğrudan yazmaz (statik tarama); olay servisi olay durumunu değiştirmez.

EVENT_TOOL_RISK = {
    "event.list_alerts": "R0",
    "event.correlate": "R0",
    "event.acknowledge": "R1",
    "event.create_incident": "R1",
    "event.silence": "R2",
}
EVENT_READ_TOOLS = {"event.list_alerts": {"status": "firing"}, "event.correlate": {}}


def test_gate_events_tools_risk_classes_and_policy():
    from serviszeka.tools import registry

    for name, risk in EVENT_TOOL_RISK.items():
        spec = registry.get(name)
        assert spec.risk_class.value == risk, name
        assert (spec.domain, spec.verb) in POLICIES, name
    assert registry.get("event.create_incident").risk_fn is not None
    assert POLICIES[("event", "silence")].max_auto_risk is None
    assert POLICIES[("incident", "resolve")].max_auto_risk is None


@pytest.mark.parametrize("name", sorted(EVENT_READ_TOOLS))
async def test_gate_events_ai_read_writes_nothing(session, name):
    from _events import seed_p1_group

    from serviszeka.models import (
        ActionRequest,
        AIDecision,
        AuditLog,
        EventGroup,
        Incident,
        ToolInvocation,
    )
    from serviszeka.tools import ToolStatus, execute_tool

    group_id = await seed_p1_group(session)
    version = (await session.get(EventGroup, group_id)).version
    before = await session.scalar(select(func.count()).select_from(AuditLog))
    r = await execute_tool(
        session, AI_TOOL_USER, name, EVENT_READ_TOOLS[name], idempotency_key="k-1",
        confidence=1.0, settings=LOOSE,
    )  # fmt: skip
    assert r.status == ToolStatus.EXECUTED, r.error
    for model in (AIDecision, ActionRequest, ToolInvocation, Incident):
        assert await session.scalar(select(func.count()).select_from(model)) == 0, model
    actions = (await session.scalars(select(AuditLog.action).order_by(AuditLog.seq))).all()
    assert len(actions) == before + 1 and actions[-1] == "tool.read"
    assert (await session.get(EventGroup, group_id)).version == version


async def test_gate_events_p1_group_never_opens_incident_without_approval(session):
    from _events import seed_p1_group

    from serviszeka.agents import events as event_agent
    from serviszeka.models import ActionRequest, Incident

    group_id = await seed_p1_group(session)
    for _ in range(2):  # ikinci işlem yeni istek açmaz
        [out] = await event_agent.process_groups(session, [group_id], settings=LOOSE)
    assert await session.scalar(select(func.count()).select_from(Incident)) == 0
    ars = (await session.scalars(select(ActionRequest))).all()
    assert len(ars) == 1 and ars[0].risk_class == "R2" and ars[0].state == "pending"
    assert ars[0].tool == "event.create_incident" and ars[0].requested_by == "ai:events"
    assert out.action == "skipped" and "bekleyen" in out.reason


async def test_gate_events_resolved_alerts_never_resolve_incident(session):
    from datetime import timedelta

    from _events import EV_SETTINGS, T0, am_alert, am_payload, seed_low_group

    from serviszeka.agents import events as event_agent
    from serviszeka.models import ActionRequest, AuditLog, EventGroup, Incident
    from serviszeka.services import events

    loose = EV_SETTINGS.model_copy(update={"ai_shadow_mode": False, "ai_confidence_threshold": 0.0})
    group_id = await seed_low_group(session, loose)
    [out] = await event_agent.process_groups(
        session, [group_id], settings=loose, now=T0 + timedelta(seconds=10)
    )
    assert out.action == "create" and out.status == "executed"  # R1 (P4) politika gereği otomatik
    group = await session.get(EventGroup, group_id)
    alerts = events.parse_alertmanager(
        am_payload(am_alert("fp-yazici", status="resolved", severity="error", ci="test-yazici",
                            ends=T0 + timedelta(minutes=5)), status="resolved")
    )  # fmt: skip
    res = await events.ingest(session, alerts, settings=loose, now=T0 + timedelta(minutes=6))
    later = T0 + timedelta(minutes=7)
    outs = await event_agent.process_groups(session, res.group_ids, settings=loose, now=later)
    assert [o.action for o in outs] == ["propose_resolve"]
    incident = await session.get(Incident, group.incident_id)
    assert incident.status.value != "resolved" and incident.resolved_at is None
    ar = await session.scalar(select(ActionRequest).where(ActionRequest.tool == "incident.resolve"))
    assert ar.state == "pending" and ar.risk_class == "R2" and ar.requested_by == "ai:events"
    notes = (await session.scalars(select(AuditLog).where(AuditLog.action == "incident.work_note"))).all()
    assert len(notes) == 1 and "ÇÖZÜLDÜ" in notes[0].details["note"]
    # Tekrar işlem: ikinci çözüm önerisi yok
    await event_agent.process_groups(session, res.group_ids, settings=loose, now=later)
    assert await session.scalar(
        select(func.count()).select_from(ActionRequest).where(ActionRequest.tool == "incident.resolve")
    ) == 1


def test_gate_events_static_no_direct_writes_or_status_change():
    agent = AGENTS_DIR / "events.py"
    scan = scan_agent_source(agent.read_text(encoding="utf-8"), "events")
    assert scan.problems == [] and scan.writes is False  # yazma yalnızca execute_tool ile
    banned = re.compile(
        r"incident\w*\.status\s*=(?!=)|[\"']status[\"']\s*:|IncidentStatus\.(RESOLVED|CLOSED)"
        r"|incident_service\.(update_incident|apply_changes)\([^)]*status"
    )
    for rel in ("services/events.py", "tools/builtin/event.py"):
        source = (TOOLS_DIR / rel).read_text(encoding="utf-8")
        assert not banned.search(source), (rel, banned.search(source).group(0))


# --- 9) Faz 4: sürekli iyileştirme (csi.*, agents/improvement.py `ai:csi`) ------------------------
#
# Kapı: yapay zekâ iyileştirmeyi yalnızca fikir (`idea`) olarak açar ve uygulaması bitmiş kaydı
# ölçer; asla onaylamaz, başlatmaz, uygulandı işaretlemez, kapatmaz, reddetmez. Bunlar araç
# değildir (csi alanında yalnızca 4 araç), servis `ai:` aktörü/`ai_agent` rolünü reddeder, API
# approver/admin ister. En gevşek politikada bile ajan çalışması onaylı/kapalı kayıt üretmez.

CSI_TOOL_RISK = {
    "csi.list": "R0",
    "csi.detect_signals": "R0",
    "csi.create_improvement": "R1",
    "csi.measure_improvement": "R1",
}
CSI_HUMAN_ONLY = ("approve", "reject", "start", "mark_implemented", "close")


def test_gate_csi_tools_risk_classes_and_policy():
    from serviszeka.tools import registry

    csi = {n for n in registry.names() if registry.get(n).domain == "csi"}
    assert csi == set(CSI_TOOL_RISK)  # onay/kapatma aracı eklenemez (kapı güncellenmeden)
    for name, risk in CSI_TOOL_RISK.items():
        spec = registry.get(name)
        assert spec.risk_class.value == risk, name
        assert POLICIES[(spec.domain, spec.verb)].max_auto_risk == RiskLevel.LOW, name
        if risk == "R0":
            assert spec.risk_fn is None, name
    fields = set(registry.get("csi.create_improvement").input_model.model_fields)
    assert not fields & {
        "state", "approved_by", "approved_at", "closed_by", "closed_at", "started_at",
        "implemented_at", "measured_value", "target_met", "measurement", "created_by",
        "detected_by",
    }  # fmt: skip


@pytest.mark.parametrize(
    "actor",
    [
        CurrentUser("ai:csi", frozenset({"ai_agent"})),
        CurrentUser("ai:csi", frozenset({"ai_agent", "admin", "approver"})),
        CurrentUser("mcp-istemci", frozenset({"ai_agent", "approver"})),
    ],
    ids=["ai", "ai-admin", "ai-agent-rolu"],
)
async def test_gate_csi_ai_cannot_approve_start_close_or_reject(session, actor):
    from serviszeka.models import Improvement
    from serviszeka.services import improvements as svc
    from serviszeka.services.improvement_schemas import ImprovementCreate

    human = CurrentUser("ayse", frozenset({"agent"}))
    imp = await svc.create_improvement(session, ImprovementCreate(title="Fikir"), human)
    await session.commit()
    for name in CSI_HUMAN_ONLY:
        with pytest.raises(svc.ImprovementForbiddenError):
            kw = {} if name == "mark_implemented" else {"note": "x"}
            await getattr(svc, name)(session, imp.id, actor, **kw)
    await session.refresh(imp)
    assert imp.state == "idea" and imp.version == 1 and imp.approved_by is None
    assert await session.scalar(select(func.count()).select_from(Improvement)) == 1


async def test_gate_csi_api_ai_token_and_agent_cannot_approve_or_close(session):
    from serviszeka.services import improvements as svc
    from serviszeka.services.improvement_schemas import ImprovementCreate

    imp = await svc.create_improvement(
        session, ImprovementCreate(title="Fikir"), CurrentUser("ayse", frozenset({"agent"}))
    )
    await session.commit()

    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            for headers in (dev_headers("bot", "ai_agent"), AGENT,
                            dev_headers("ali", "requester")):  # fmt: skip
                for step in ("approve", "reject", "close"):
                    r = await c.post(f"/improvements/{imp.id}/{step}", json={"note": "x"},
                                     headers=headers)  # fmt: skip
                    assert r.status_code == 403, (step, r.status_code)
            r = await c.post("/improvements/review", headers=dev_headers("bot", "ai_agent"))
            assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()
    await session.refresh(imp)
    assert imp.state == "idea" and imp.version == 1


async def test_gate_csi_agent_under_loosest_policy_only_proposes_and_measures(
    session, monkeypatch
):
    from _csi import AGENT as CSI_AGENT
    from _csi import APPROVER as CSI_APPROVER
    from _csi import NOW, Frozen, mttr_rows, seed_signals

    from serviszeka.agents import improvement as csi_agent
    from serviszeka.models import ActionRequest, Improvement
    from serviszeka.services import improvements as svc
    from serviszeka.services.improvement_schemas import ImprovementCreate

    await seed_signals(session, Frozen(monkeypatch))
    # Ölçümü gelmiş, insanca onaylanıp uygulanmış bir iyileştirme
    t0 = NOW - timedelta(days=40)
    imp = await svc.create_improvement(
        session, ImprovementCreate(title="MTTR", kpi_key="mttr", measure_days=30), CSI_AGENT,
        now=t0,
    )  # fmt: skip
    await svc.approve(session, imp.id, CSI_APPROVER, now=t0)
    await svc.start(session, imp.id, CSI_AGENT, now=t0)
    await svc.mark_implemented(session, imp.id, CSI_AGENT, at=t0, now=t0)
    await mttr_rows(session, group="network_ops", category="ag", hours=3, start=t0 + timedelta(days=1))
    await session.commit()

    out = await csi_agent.run_review(session, llm=FakeLLM([{"narrative": "Onayla ve kapat."}]),
                                     settings=LOOSE, now=NOW)  # fmt: skip
    await session.commit()
    assert out["proposed"] >= 8 and out["measured"] == 1
    rows = (await session.scalars(select(Improvement))).all()
    for row in rows:
        if row.id == imp.id:
            assert row.state == "measured" and row.closed_at is None and row.closed_by is None
            continue
        # LLM metni "onayla" dese de yapay zekâ yalnızca fikir açar
        assert row.state == "idea" and row.approved_by is None and row.closed_at is None
        assert row.created_by == "ai:csi"
    assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 0


def test_gate_csi_static_agent_writes_only_through_tools():
    agent = AGENTS_DIR / "improvement.py"
    source = agent.read_text(encoding="utf-8")
    scan = scan_agent_source(source, "improvement")
    assert scan.problems == [] and scan.writes is False  # yazma yalnızca execute_tool ile
    human_only = re.compile(
        r"\.(approve|reject|start|mark_implemented|close)\(|ImprovementState\.(APPROVED|CLOSED|"
        r"REJECTED|IN_PROGRESS)|[\"']state[\"']\s*:"
    )
    for rel in ("agents/improvement.py", "tools/builtin/csi.py"):
        text = (TOOLS_DIR / rel).read_text(encoding="utf-8")
        assert not human_only.search(text), (rel, human_only.search(text).group(0))
