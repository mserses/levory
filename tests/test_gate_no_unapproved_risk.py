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
    "incident.resolve": lambda fx: {"incident_id": fx.incident_id, "resolution": "Çözüldü"},
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
}
# Koşullu riskin R2'ye çıktığı R1 araç çağrıları (P1/P2 önceliği, SLA saatini durduran bekleme)
CONDITIONAL_RISKY = {
    "incident.create/p1": (
        "incident.create",
        lambda fx: {"title": "Tüm şirket", "caller": "ali", "impact": "high", "urgency": "high"},
    ),
    "incident.set_priority/p1": (
        "incident.set_priority",
        lambda fx: {"incident_id": fx.incident_id, "impact": "high", "urgency": "high"},
    ),
    "incident.set_priority/p2": (
        "incident.set_priority",
        lambda fx: {"incident_id": fx.incident_id, "impact": "high", "urgency": "medium"},
    ),
    "incident.set_on_hold/awaiting_caller": (
        "incident.set_on_hold",
        lambda fx: {"incident_id": fx.incident_id, "hold_reason": "awaiting_caller"},
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
    return replace(fx, problem_id=problem.id)


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
    from serviszeka.services import requests

    d = _GATE_DIRECTORY["d"]
    _, zeynep, ayse, mehmet = _req_users()
    d.add_sku("sku-spe-e3", "SPE_E3", enabled=100, consumed=100)  # boş lisans yok -> adım başarısız
    req = await _open(session, "new_employee_account", "mehmet", mehmet,
                      {"user": "yeni.calisan", "mailbox": "muhasebe"})
    req = await requests.decide(session, req.id, zeynep, "approve", settings=LOOSE_STD)
    assert req.state == "failed", (req.state, req.error)
    with pytest.raises(requests.RequestStateError):
        await requests.update_form(session, req.id, ayse, {"user": "ali", "mailbox": "muhasebe"})
    assert req.form_values["user"] == "yeni.calisan"
    d.add_sku("sku-spe-e3", "SPE_E3", enabled=100, consumed=10)
    req = await requests.retry(session, req.id, ayse, settings=LOOSE_STD)
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
