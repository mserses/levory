"""Değişiklik risk ajanı (agents/change_risk): deterministik skor + LLM anlatısı + politika.

Yapay zekâ yalnızca risk alanlarını önerir/yazar (politika izin verirse); asla durum geçişi ya da
onay yapmaz. LLM yalnızca Türkçe anlatı, kontrol listesi ve en fazla ±10 düzeltme üretir.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _auth import AGENT, dev_headers
from sqlalchemy import func, select

from serviszeka import audit, db
from serviszeka.agents import change_risk
from serviszeka.api import changes as changes_api
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import (
    ChangeState,
    ChangeType,
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    DecisionOutcome,
    RiskLevel,
)
from serviszeka.llm import FakeLLM, LLMError
from serviszeka.main import app
from serviszeka.models import AuditLog, ChangeApproval, ChangeRequest
from serviszeka.policy import POLICIES
from serviszeka.services import changes, cmdb
from serviszeka.services import incidents as incident_service
from serviszeka.services.change_schemas import ChangeCreate, TemplateCreate
from serviszeka.services.cmdb_schemas import CICreate
from serviszeka.services.schemas import IncidentCreate

St = ChangeState
REQ = CurrentUser("ayse", frozenset({"agent"}))
MGR = CurrentUser("mudur", frozenset({"change_manager"}))

SHADOW = Settings(ai_shadow_mode=True, cab_min_approvals=2)
LIVE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.75, cab_min_approvals=2)
NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
T0 = NOW + timedelta(days=9)


def _llm(**kw) -> FakeLLM:
    data = {
        "narrative": "Üretim veritabanı yaması ödeme hizmetini etkileyebilir.",
        "checklist": ["Yedeği doğrulayın", "Bakım penceresini duyurun"],
        "adjustment": 0,
        "justification": "",
        "confidence": 0.9,
    }
    data.update(kw)
    return FakeLLM([data])


async def _ci(session, name, ci_class=CIClass.SERVER, criticality=4, env=CIEnvironment.DEV):
    ci = await cmdb.create_ci(
        session,
        CICreate(name=name, ci_class=ci_class, criticality=criticality, environment=env),
        "mudur",
    )
    return ci.id


async def _change(session, ci_ids=(), **kw) -> ChangeRequest:
    fields = {
        "title": "Veritabanı yaması",
        "description": "Sorumlu: ali.veli@ornek.com",
        "change_type": ChangeType.NORMAL,
        "implementation_plan": "Yamayı uygula",
        "backout_plan": "Anlık görüntüden geri dön ve servisi yeniden başlat",
        "test_plan": "Duman testi",
        "planned_start": T0,
        "planned_end": T0 + timedelta(hours=2),
        "assignee": "mehmet",
        "affected_ci_ids": list(ci_ids),
    }
    fields.update(kw)
    return await changes.create_change(session, ChangeCreate(**fields), REQ)


async def _prod_db_with_service(session):
    db_ci = await _ci(session, "odeme-db", CIClass.DATABASE, 1, CIEnvironment.PROD)
    svc = await _ci(session, "odeme", CIClass.SERVICE, 2, CIEnvironment.PROD)
    await cmdb.add_relationship(session, svc, db_ci, CIRelationshipType.DEPENDS_ON, "mudur")
    return db_ci, svc


async def _past_change(session, ci_ids, outcome, ended):
    ch = await _change(session, ci_ids)
    ch.state = St.CLOSED if outcome == "succeeded" else St.FAILED
    ch.implementation_outcome = outcome
    ch.actual_end = ended
    await session.flush()
    return ch


async def _assess(session, ch, llm=None, settings=SHADOW):
    return await change_risk.assess(session, ch, llm or _llm(), settings, now=NOW)


# --- deterministik skor -------------------------------------------------------------------


async def test_deterministic_breakdown(session):
    db_ci, _ = await _prod_db_with_service(session)
    ch = await _change(session, [db_ci])
    d = await _assess(session, ch)
    det = d.output["deterministic"]
    assert det["factors"] == {
        "change_type": 15,
        "criticality": 35,
        "production": 10,
        "business_services": 5,
        "failure_history": 0,
        "recent_incidents": 0,
        "backout_plan": 0,
    }
    assert det["score"] == 65 and det["level"] == "high"
    assert det["inputs"]["highest_criticality"] == 1
    assert det["inputs"]["business_services"] == ["odeme"]
    assert det["inputs"]["recent_incidents"] == 0 and det["notes"] == []


async def test_recent_incidents_on_root_cis_last_30_days(session):
    ci = await _ci(session, "app", CIClass.APPLICATION, 4)
    other = await _ci(session, "baska", CIClass.APPLICATION, 4)
    for ci_id, age in ((ci, 2), (ci, 29), (ci, 40), (other, 1)):
        inc = await incident_service.create_incident(
            session, IncidentCreate(title="Uygulama hatası", caller="ayse"), actor="ayse"
        )
        await incident_service.set_affected_cis(session, inc.id, [ci_id], "ayse")
        inc.created_at = NOW - timedelta(days=age)
    await session.flush()
    ch = await _change(session, [ci])
    det = (await _assess(session, ch)).output["deterministic"]
    assert det["inputs"]["recent_incidents"] == 2
    assert det["factors"]["recent_incidents"] == 10


async def test_without_incident_link_component_is_zero_and_noted(session, monkeypatch):
    import serviszeka.models as models_module

    monkeypatch.delattr(models_module, "incident_cis")
    ch = await _change(session, [await _ci(session, "app", CIClass.APPLICATION, 4)])
    det = (await _assess(session, ch)).output["deterministic"]
    assert det["inputs"]["recent_incidents"] is None
    assert det["factors"]["recent_incidents"] == 0
    assert det["notes"] == [change_risk.NO_INCIDENT_LINK_NOTE]


def test_score_formula_is_documented_and_bounded():
    assert change_risk.score_breakdown(
        change_type=ChangeType.EMERGENCY,
        highest_criticality=1,
        production=True,
        business_services=10,
        failure_share=1.0,
        recent_incidents=10,
        backout_plan="",
    ) == {
        "change_type": 30,
        "criticality": 35,
        "production": 10,
        "business_services": 20,
        "failure_history": 20,
        "recent_incidents": 15,
        "backout_plan": 10,
    }
    assert change_risk.clamp_score(140) == 100 and change_risk.clamp_score(-3) == 0


@pytest.mark.parametrize(
    ("plan", "points"),
    [("", 10), ("   ", 10), ("geri al", 5), ("Anlık görüntüden geri dön ve doğrula", 0)],
)
def test_backout_plan_points(plan, points):
    assert change_risk.backout_points(plan) == points


async def test_failure_history_last_180_days(session):
    ci = await _ci(session, "app", CIClass.APPLICATION, 4)
    other = await _ci(session, "baska", CIClass.APPLICATION, 4)
    await _past_change(session, [ci], "failed", NOW - timedelta(days=10))
    await _past_change(session, [ci], "succeeded", NOW - timedelta(days=20))
    await _past_change(session, [ci], "failed", NOW - timedelta(days=200))  # pencere dışı
    await _past_change(session, [other], "failed", NOW - timedelta(days=5))  # başka CI
    ch = await _change(session, [ci])
    d = await _assess(session, ch)
    det = d.output["deterministic"]
    assert det["inputs"]["past_changes"] == 2 and det["inputs"]["past_failures"] == 1
    assert det["factors"]["failure_history"] == 10


# --- LLM -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("adj", "applied"), [(4, 4), (25, 10), (-25, -10), (-7, -7)])
async def test_llm_adjustment_is_bounded(session, adj, applied):
    ch = await _change(session)
    d = await _assess(session, ch, _llm(adjustment=adj, justification="Benzer yama sorun çıkardı"))
    det = d.output["deterministic"]["score"]
    assert d.output["llm"]["adjustment"] == applied
    assert d.output["proposed"]["risk_score"] == change_risk.clamp_score(det + applied)


async def test_adjustment_without_justification_ignored(session):
    ch = await _change(session)
    d = await _assess(session, ch, _llm(adjustment=8, justification="  "))
    assert d.output["llm"]["adjustment"] == 0
    assert d.output["proposed"]["risk_score"] == d.output["deterministic"]["score"]


async def test_final_score_clamped(session):
    db_ci, _ = await _prod_db_with_service(session)
    ch = await _change(session, [db_ci], change_type=ChangeType.EMERGENCY, backout_plan="")
    d = await _assess(session, ch, _llm(adjustment=10, justification="Kritik"))
    assert d.output["proposed"]["risk_score"] <= 100


async def test_llm_gets_masked_prompt_and_fast_model(session):
    ch = await _change(session)
    llm = _llm()
    d = await _assess(session, ch, llm)
    call = llm.calls[0]
    assert "ali.veli@ornek.com" not in call["user"]
    assert call["model"] == SHADOW.llm_fast_model and d.model == SHADOW.llm_fast_model
    assert "Türkçe" in call["system"]
    assert d.output["llm"]["narrative"].startswith("Üretim")
    assert d.output["llm"]["checklist"] == ["Yedeği doğrulayın", "Bakım penceresini duyurun"]
    assert d.rationale == d.output["llm"]["narrative"]
    assert "ali.veli@ornek.com" not in d.input_summary


async def test_llm_failure_escalates_and_keeps_deterministic(session):
    def boom(system, user):
        raise LLMError("zaman aşımı")

    ch = await _change(session)
    d = await _assess(session, ch, FakeLLM(boom), LIVE)
    assert d.outcome == DecisionOutcome.ESCALATED and d.confidence == 0.0
    assert d.output["deterministic"]["score"] > 0 and "error" in d.output["llm"]
    assert (await changes.get_change(session, ch.id)).risk_score is None


async def test_invalid_llm_output_escalates(session):
    ch = await _change(session)
    d = await _assess(session, ch, FakeLLM([{"narrative": 5, "confidence": "çok"}]), LIVE)
    assert d.outcome == DecisionOutcome.ESCALATED
    assert (await changes.get_change(session, ch.id)).risk_score is None


# --- politika ve kayıt -------------------------------------------------------------------------


def test_policy_entry_exists_and_is_low():
    p = POLICIES[(change_risk.AGENT, change_risk.ACTION)]
    assert change_risk.AGENT == "change_risk" and change_risk.ACTION == "assess_risk"
    assert p.max_auto_risk == RiskLevel.LOW


async def test_records_decision_and_audit(session):
    ch = await _change(session)
    d = await _assess(session, ch)
    assert (d.agent, d.action, d.change_id) == ("change_risk", "assess_risk", ch.id)
    assert d.output["change_version"] == ch.version
    row = await session.scalar(
        select(AuditLog).where(AuditLog.action == "ai_decision.change_risk")
    )
    assert row.actor == "ai:change_risk" and row.entity_id == d.id
    assert row.details["outcome"] == d.outcome.value
    assert await audit.verify_chain(session)


async def test_shadow_mode_only_suggests(session):
    ch = await _change(session)
    d = await _assess(session, ch, settings=SHADOW)
    assert d.outcome == DecisionOutcome.SUGGESTED
    ch = await changes.get_change(session, ch.id)
    assert ch.risk_score is None and ch.version == 1 and ch.state == St.DRAFT


async def test_live_raise_applies_risk_fields_only(session):
    db_ci, _ = await _prod_db_with_service(session)
    ch = await _change(session, [db_ci])
    ch = await changes.transition(session, ch.id, St.ASSESS, REQ, settings=LIVE)
    ch.risk_score = 10  # insan/servis tarafından daha düşük bir skor
    await session.flush()
    d = await _assess(session, ch, _llm(adjustment=5, justification="Geçmiş kesinti"), LIVE)
    assert d.outcome == DecisionOutcome.AUTO_APPLIED and d.output["applied"] is True
    ch = await changes.get_change(session, ch.id)
    assert ch.risk_score == 70 and ch.risk_level == RiskLevel.HIGH
    assert ch.state == St.ASSESS  # durum asla değişmez
    assert await session.scalar(select(func.count()).select_from(ChangeApproval)) == 0


async def test_lowering_needs_change_manager_approval(session):
    ch = await _change(session)
    ch.risk_score = 90
    ch.risk_level = RiskLevel.CRITICAL
    await session.flush()
    d = await _assess(session, ch, _llm(adjustment=-10, justification="Yalnızca test ortamı"), LIVE)
    assert d.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert (await changes.get_change(session, ch.id)).risk_score == 90
    with pytest.raises(changes.ChangeForbiddenError):
        await changes.approve_ai_decision(session, d.id, REQ)
    await changes.approve_ai_decision(session, d.id, MGR)
    ch = await changes.get_change(session, ch.id)
    assert ch.risk_score == d.output["proposed"]["risk_score"] and ch.state == St.DRAFT


async def test_low_confidence_escalated(session):
    ch = await _change(session)
    d = await _assess(session, ch, _llm(confidence=0.3), LIVE)
    assert d.outcome == DecisionOutcome.ESCALATED
    assert (await changes.get_change(session, ch.id)).risk_score is None


async def test_raise_converts_ineligible_standard_to_normal(session):
    tpl = await changes.create_template(
        session,
        TemplateCreate(name="Yama", max_risk="low", allowed_ci_classes=["database", "service"]),
        MGR,
    )
    db_ci, _ = await _prod_db_with_service(session)
    ch = await _change(session, [db_ci], change_type=ChangeType.STANDARD, template_id=tpl.id)
    d = await _assess(session, ch, settings=LIVE)
    assert d.outcome == DecisionOutcome.AUTO_APPLIED
    ch = await changes.get_change(session, ch.id)
    assert ch.change_type == ChangeType.NORMAL and ch.state == St.DRAFT


async def test_not_allowed_after_authorization_started(session):
    ch = await _change(session)
    ch.state = St.AUTHORIZE
    await session.flush()
    with pytest.raises(changes.InvalidTransitionError):
        await _assess(session, ch)


# --- API ---------------------------------------------------------------------------------------

H_MGR = dev_headers("mudur", "change_manager")
H_CAB = dev_headers("kurul1", "cab")
H_REQUESTER = dev_headers("zeynep", "requester")
H_APPROVER = dev_headers("sef.ali", "approver")


@pytest.fixture
async def client(session):
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    state = {"llm": _llm(), "settings": LIVE}
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[changes_api.get_change_settings] = lambda: state["settings"]
    app.dependency_overrides[changes_api.get_change_llm] = lambda: state["llm"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, state
    app.dependency_overrides.clear()


async def _api_change(c) -> dict:
    body = {
        "title": "Yama",
        "change_type": "normal",
        "implementation_plan": "uygula",
        "backout_plan": "Anlık görüntüden geri dön ve doğrula",
        "planned_start": T0.isoformat(),
        "planned_end": (T0 + timedelta(hours=2)).isoformat(),
    }
    r = await c.post("/changes", json=body, headers=AGENT)
    assert r.status_code == 201, r.text
    return r.json()


async def test_api_assess_roles_and_effect(client):
    c, _ = client
    ch = await _api_change(c)
    url = f"/changes/{ch['id']}/assess"
    for h in (H_CAB, H_REQUESTER, H_APPROVER):
        assert (await c.post(url, headers=h)).status_code == 403
    r = await c.post(url, headers=AGENT)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["agent"] == "change_risk" and d["outcome"] == "auto_applied"
    after = (await c.get(f"/changes/{ch['id']}", headers=AGENT)).json()
    assert after["state"] == "draft" and after["risk_score"] == d["output"]["proposed"]["risk_score"]
    assert (await c.post(url, headers=H_MGR)).status_code == 200
    r = await c.get(f"/changes/{ch['id']}/decisions", headers=H_CAB)
    assert len(r.json()) == 2
    assert (await c.post("/changes/yok/assess", headers=AGENT)).status_code == 404


async def test_api_assess_wrong_state_422(client, session):
    c, _ = client
    ch = await _api_change(c)
    row = await session.get(ChangeRequest, ch["id"])
    row.state = St.SCHEDULED
    await session.commit()
    assert (await c.post(f"/changes/{ch['id']}/assess", headers=AGENT)).status_code == 422
