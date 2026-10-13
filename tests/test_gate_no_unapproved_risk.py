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
