"""Bayat onay: karar verildikten sonra olay değiştiyse onay reddedilir."""

import httpx
import pytest
from _auth import ADMIN, AGENT, APPROVER
from sqlalchemy import select

from serviszeka import audit, db
from serviszeka.agents import service_desk
from serviszeka.api import incidents as incidents_api
from serviszeka.config import Settings
from serviszeka.domain.enums import DecisionOutcome, Priority
from serviszeka.llm import FakeLLM
from serviszeka.main import app
from serviszeka.models import AuditLog
from serviszeka.services import incidents as svc
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate

LIVE = Settings(ai_shadow_mode=False, anthropic_api_key=None)
SHADOW = Settings(ai_shadow_mode=True, anthropic_api_key=None)
STALE_MSG = "Kayıt öneriden sonra değişti; yeniden triage gerekli"


def _response(**kw):
    data = {
        "kind": "incident",
        "category": "ag",
        "service": "vpn",
        "impact": "medium",
        "urgency": "medium",
        "assignment_group": "network_ops",
        "suggested_resolution": None,
        "kb_article_ids": [],
        "confidence": 0.9,
        "rationale": "test",
    }
    data.update(kw)
    return data


async def _incident(session, impact="low", urgency="low"):
    return await svc.create_incident(
        session,
        IncidentCreate(title="Ağ yavaş", caller="ali", impact=impact, urgency=urgency),
        actor="ali",
    )


async def _actions(session):
    return [r.action for r in (await session.scalars(select(AuditLog).order_by(AuditLog.seq)))]


async def test_decision_stores_incident_version_without_apply(session):
    inc = await _incident(session)
    decision = await service_desk.triage(session, inc, FakeLLM([_response()]), settings=SHADOW)
    assert decision.outcome == DecisionOutcome.SUGGESTED
    assert decision.incident_version == 1


async def test_decision_stores_post_apply_version_after_partial_apply(session):
    inc = await _incident(session)  # P5
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(impact="medium", urgency="high")]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert decision.output["applied_fields"]  # sınıflandırma kısmen uygulandı
    assert (await svc.get_incident(session, inc.id)).version == 2
    assert decision.incident_version == 2
    # Kendi kısmi uygulaması onayı bayatlatmaz
    await service_desk.approve_decision(session, decision.id, "sef.ali")
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P2


async def test_escalated_decision_stores_version(session):
    inc = await _incident(session)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(confidence=0.1)]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.ESCALATED
    assert decision.incident_version == 1


async def test_approve_rejected_when_incident_changed(session):
    inc = await _incident(session)
    decision = await service_desk.triage(session, inc, FakeLLM([_response()]), settings=SHADOW)
    await svc.update_incident(session, inc.id, IncidentUpdate(category="insan"), "insan")

    with pytest.raises(service_desk.StaleDecisionError) as exc_info:
        await service_desk.approve_decision(session, decision.id, "sef.ali")
    assert isinstance(exc_info.value, service_desk.DecisionError)
    assert STALE_MSG in str(exc_info.value)

    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.category == "insan" and refreshed.version == 2  # insan verisi korunur
    dec = await service_desk.get_decision(session, decision.id)
    assert dec.approved_by is None and dec.output["applied"] is False
    last = await session.scalar(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1))
    assert last.action == "ai_decision.approve_rejected" and last.actor == "sef.ali"
    assert last.entity_id == decision.id
    assert last.details["decision_version"] == 1 and last.details["incident_version"] == 2
    assert await audit.verify_chain(session)


async def test_concurrent_edit_during_triage_makes_decision_stale(session):
    inc = await _incident(session)

    async def edit_then_answer(system, user):
        await svc.update_incident(session, inc.id, IncidentUpdate(category="insan"), "insan")
        return _response()

    decision = await service_desk.triage(session, inc, FakeLLM(edit_then_answer), settings=LIVE)
    assert decision.outcome == DecisionOutcome.SUGGESTED
    # Karar, ajanın gördüğü (eski) sürüme dayanır
    assert decision.incident_version == 1
    with pytest.raises(service_desk.StaleDecisionError):
        await service_desk.approve_decision(session, decision.id, "sef.ali")
    assert (await svc.get_incident(session, inc.id)).category == "insan"


async def test_already_approved_still_plain_decision_error(session):
    inc = await _incident(session)
    decision = await service_desk.triage(session, inc, FakeLLM([_response()]), settings=SHADOW)
    await service_desk.approve_decision(session, decision.id, "sef.ali")
    with pytest.raises(service_desk.DecisionError) as exc_info:
        await service_desk.approve_decision(session, decision.id, "baska")
    assert not isinstance(exc_info.value, service_desk.StaleDecisionError)
    assert "zaten" in str(exc_info.value)


async def test_api_stale_approval_returns_409_and_audit_persisted(session):
    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: FakeLLM([_response()])
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: SHADOW
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=AGENT
        ) as client:
            inc = (await client.post("/incidents", json={"title": "VPN", "caller": "a"})).json()
            dec = (await client.post(f"/incidents/{inc['id']}/triage")).json()
            assert dec["incident_version"] == 1
            r = await client.patch(f"/incidents/{inc['id']}", json={"assignee": "veli"})
            assert r.status_code == 200
            r = await client.post(f"/incidents/decisions/{dec['id']}/approve", headers=APPROVER)
            assert r.status_code == 409
            assert r.json()["detail"] == STALE_MSG
            after = (await client.get(f"/incidents/{inc['id']}")).json()
            assert after["version"] == 2 and after["category"] is None
            r = await client.get("/audit/verify", headers=ADMIN)
            assert r.json() == {"valid": True}
    finally:
        app.dependency_overrides.clear()
    # Red kaydı commit edildi (ayrı oturumdan okunur)
    async with db.get_sessionmaker()() as s:
        assert "ai_decision.approve_rejected" in await _actions(s)


async def test_legacy_decision_without_version_is_refused(session):
    # 0003 öncesi kararlar: sürüm bilinmiyor -> bayatlık doğrulanamaz, onay reddedilir
    inc = await _incident(session)
    decision = await service_desk.triage(session, inc, FakeLLM([_response()]), settings=SHADOW)
    decision.incident_version = None
    await session.flush()
    await svc.update_incident(session, inc.id, IncidentUpdate(category="insan"), "insan")

    with pytest.raises(service_desk.StaleDecisionError):
        await service_desk.approve_decision(session, decision.id, "sef.ali")
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.category == "insan" and refreshed.version == 2
    dec = await service_desk.get_decision(session, decision.id)
    assert dec.approved_by is None
    assert (await _actions(session))[-1] == "ai_decision.approve_rejected"
