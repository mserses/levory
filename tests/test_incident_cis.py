"""Olay <-> CI bağı: servis, API ve Hizmet Masası triage'ında CMDB etki kanıtı (taban kural)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from _auth import ADMIN, AGENT, APPROVER, dev_headers
from sqlalchemy import select

from serviszeka import audit, db, notifications
from serviszeka.agents import service_desk
from serviszeka.api import incidents as incidents_api
from serviszeka.config import Settings
from serviszeka.domain.enums import (
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    CIStatus,
    DecisionOutcome,
    Impact,
    IncidentStatus,
    Priority,
)
from serviszeka.llm import FakeLLM
from serviszeka.main import app
from serviszeka.models import AuditLog
from serviszeka.services import cmdb
from serviszeka.services import incidents as svc
from serviszeka.services.cmdb_schemas import CICreate
from serviszeka.services.schemas import IncidentCreate, IncidentRead, IncidentUpdate

LIVE = Settings(ai_shadow_mode=False, anthropic_api_key=None)
SHADOW = Settings(ai_shadow_mode=True, anthropic_api_key=None)
REQUESTER = dev_headers("zeynep", "requester")
R = CIRelationshipType


async def _ci(session, name, ci_class=CIClass.APPLICATION, criticality=3, **kw):
    return await cmdb.create_ci(
        session, CICreate(name=name, ci_class=ci_class, criticality=criticality, **kw), "mudur"
    )


async def _incident(session, **kw):
    data = {"title": "ERP yavaş", "description": "Ekran açılmıyor", "caller": "ayse"}
    data.update(kw)
    return await svc.create_incident(session, IncidentCreate(**data), actor="ayse")


# --- servis --------------------------------------------------------------------------


async def test_set_affected_cis_links_bumps_version_and_audits(session):
    a = await _ci(session, "ERP-App")
    b = await _ci(session, "ERP-DB", CIClass.DATABASE)
    inc = await _incident(session)
    out = await svc.set_affected_cis(session, inc.id, [b.id, a.id, b.id], "ayse")
    await session.commit()
    assert sorted(out.ci_ids) == sorted([a.id, b.id])
    assert out.version == 2
    assert [c.id for c in await svc.list_affected_cis(session, inc.id)] == [a.id, b.id]  # ada göre

    # Değiştirme semantiği: yeni küme eskisinin yerini alır
    out = await svc.set_affected_cis(session, inc.id, [a.id], "ayse", expected_version=2)
    await session.commit()
    assert out.ci_ids == [a.id] and out.version == 3

    logs = (
        await session.scalars(
            select(AuditLog).where(AuditLog.action == "incident.cis.update").order_by(AuditLog.seq)
        )
    ).all()
    assert len(logs) == 2
    assert sorted(logs[0].details["added"]) == sorted([a.id, b.id])
    assert logs[1].details["removed"] == [b.id] and logs[1].details["added"] == []
    assert await audit.verify_chain(session)


async def test_set_affected_cis_same_set_is_noop(session):
    a = await _ci(session, "ERP-App")
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [a.id], "ayse")
    out = await svc.set_affected_cis(session, inc.id, [a.id], "ayse")
    assert out.version == 2


async def test_set_affected_cis_rejects_unknown_ci_and_stale_version(session):
    inc = await _incident(session)
    with pytest.raises(cmdb.CINotFoundError):
        await svc.set_affected_cis(session, inc.id, ["yok"], "ayse")
    a = await _ci(session, "ERP-App")
    with pytest.raises(svc.VersionConflictError):
        await svc.set_affected_cis(session, inc.id, [a.id], "ayse", expected_version=5)
    with pytest.raises(svc.IncidentNotFoundError):
        await svc.set_affected_cis(session, "yok", [a.id], "ayse")


async def test_closed_incident_cis_cannot_change(session):
    a = await _ci(session, "ERP-App")
    inc = await _incident(session)
    await svc.update_incident(
        session, inc.id, IncidentUpdate(status=IncidentStatus.CANCELLED), "ayse"
    )
    with pytest.raises(svc.InvalidTransitionError):
        await svc.set_affected_cis(session, inc.id, [a.id], "ayse")


async def test_incident_read_includes_ci_ids(session):
    a = await _ci(session, "ERP-App")
    inc = await _incident(session)
    assert IncidentRead.model_validate(inc).ci_ids == []
    await svc.set_affected_cis(session, inc.id, [a.id], "ayse")
    await session.commit()
    await session.refresh(inc)
    assert IncidentRead.model_validate(inc).ci_ids == [a.id]


# --- API -----------------------------------------------------------------------------


@pytest.fixture
async def ctx(session):
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    state = {"llm": FakeLLM([_response()]), "settings": LIVE}
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: state["llm"]
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: state["settings"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=AGENT
    ) as client:
        yield client, state, session
    app.dependency_overrides.clear()


async def test_api_set_and_list_incident_cis(ctx):
    client, _, session = ctx
    a = await _ci(session, "ERP-App", criticality=1)
    await session.commit()
    inc = (await client.post("/incidents", json={"title": "ERP", "caller": "ayse"})).json()
    assert inc["ci_ids"] == []

    r = await client.put(f"/incidents/{inc['id']}/cis", json={"ci_ids": [a.id]})
    assert r.status_code == 200, r.text
    assert r.json()["ci_ids"] == [a.id] and r.json()["version"] == 2
    assert r.headers["etag"] == '"2"'

    r = await client.get(f"/incidents/{inc['id']}/cis")
    assert r.status_code == 200
    assert [c["name"] for c in r.json()] == ["ERP-App"] and r.json()[0]["criticality"] == 1
    assert (await client.get(f"/incidents/{inc['id']}")).json()["ci_ids"] == [a.id]

    # İyimser kilit: If-Match ya da gövdede sürüm
    r = await client.put(
        f"/incidents/{inc['id']}/cis", json={"ci_ids": []}, headers={"If-Match": '"1"'}
    )
    assert r.status_code == 409
    r = await client.put(f"/incidents/{inc['id']}/cis", json={"ci_ids": [], "expected_version": 1})
    assert r.status_code == 409

    r = await client.put(f"/incidents/{inc['id']}/cis", json={"ci_ids": ["yok"]})
    assert r.status_code == 422
    assert (await client.put("/incidents/yok/cis", json={"ci_ids": []})).status_code == 404
    assert (await client.get("/incidents/yok/cis")).status_code == 404


async def test_api_incident_cis_roles(ctx):
    client, _, session = ctx
    a = await _ci(session, "ERP-App")
    await session.commit()
    inc = (await client.post("/incidents", json={"title": "ERP", "caller": "zeynep"})).json()
    await client.put(f"/incidents/{inc['id']}/cis", json={"ci_ids": [a.id]})

    for headers in (REQUESTER, APPROVER):
        assert (
            await client.put(f"/incidents/{inc['id']}/cis", json={"ci_ids": []}, headers=headers)
        ).status_code == 403
        assert (await client.get(f"/incidents/{inc['id']}/cis", headers=headers)).status_code == 403
    assert (await client.get(f"/incidents/{inc['id']}/cis", headers=ADMIN)).status_code == 200

    # Self-servis kullanıcı kendi kaydında CMDB iç ayrıntısını görmez
    r = await client.get(f"/incidents/{inc['id']}", headers=REQUESTER)
    assert r.status_code == 200 and r.json()["ci_ids"] == []
    listed = (await client.get("/incidents", headers=REQUESTER)).json()
    assert [i["ci_ids"] for i in listed] == [[]]


async def test_api_triage_uses_linked_ci_floor(ctx):
    client, state, session = ctx
    a = await _ci(session, "ERP-App", criticality=1)
    await session.commit()
    inc = (await client.post("/incidents", json={"title": "ERP", "caller": "ayse"})).json()
    await client.put(f"/incidents/{inc['id']}/cis", json={"ci_ids": [a.id]})
    state["llm"] = FakeLLM([_response(impact="low", urgency="low")])
    r = await client.post(f"/incidents/{inc['id']}/triage")
    assert r.status_code == 200, r.text
    out = r.json()["output"]
    assert out["impact"] == "medium" and out["cmdb"]["floor_applied"] is True
    assert out["proposed_priority"] == "P4"


# --- triage: CMDB bağlamı ve taban kural ---------------------------------------------


def _response(**kw):
    data = {
        "kind": "incident",
        "category": "is_uygulamasi",
        "service": "erp",
        "impact": "low",
        "urgency": "low",
        "assignment_group": "business_apps",
        "suggested_resolution": None,
        "kb_article_ids": [],
        "confidence": 0.95,
        "rationale": "test",
    }
    data.update(kw)
    return data


async def _two_services(session, *, env=CIEnvironment.PROD):
    """DB'ye bağlı iki iş hizmeti: DB değişirse/bozulursa ikisi de etkilenir."""
    database = await _ci(session, "Ortak-DB", CIClass.DATABASE, criticality=2, environment=env)
    app_ = await _ci(session, "Ortak-App", criticality=2, environment=env)
    s1 = await _ci(session, "Bordro", CIClass.SERVICE, criticality=2, environment=env)
    s2 = await _ci(session, "Faturalama", CIClass.SERVICE, criticality=2, environment=env)
    await cmdb.add_relationship(session, app_.id, database.id, R.DEPENDS_ON, "mudur")
    await cmdb.add_relationship(session, s1.id, app_.id, R.DEPENDS_ON, "mudur")
    await cmdb.add_relationship(session, s2.id, database.id, R.DEPENDS_ON, "mudur")
    return database


async def test_triage_prompt_contains_masked_ci_context(session):
    ci = await _ci(
        session,
        "ERP-App ali.veli@ornek.com.tr",
        criticality=1,
        description="Sahibi TCKN 10000000146",
    )
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [ci.id], "ayse")
    llm = FakeLLM([_response()])
    decision = await service_desk.triage(session, inc, llm, settings=SHADOW)
    prompt = llm.calls[0]["user"]
    assert "CMDB" in prompt and "ERP-App" in prompt
    assert "ali.veli@ornek.com.tr" not in prompt and "10000000146" not in prompt
    assert '"criticality": 1' in prompt and '"environment": "prod"' in prompt
    assert "ali.veli@ornek.com.tr" not in decision.input_summary
    assert "CMDB" in decision.input_summary


async def test_no_linked_cis_keeps_prompt_and_proposal_unchanged(session):
    inc = await _incident(session)
    llm = FakeLLM([_response()])
    decision = await service_desk.triage(session, inc, llm, settings=SHADOW)
    assert "CMDB" not in llm.calls[0]["user"]
    assert decision.output["impact"] == "low"
    assert decision.output["cmdb"] is None


async def test_criticality1_prod_ci_raises_impact_to_medium(session):
    ci = await _ci(session, "ERP-App", criticality=1)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [ci.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    decision = await service_desk.triage(session, inc, FakeLLM([_response()]), settings=LIVE)

    out = decision.output
    assert out["impact"] == "medium" and out["proposed"]["impact"] == "medium"
    assert out["llm_impact"] == "low"
    assert out["cmdb"]["impact_floor"] == "medium" and out["cmdb"]["floor_applied"] is True
    assert out["cmdb"]["ci_ids"] == [ci.id]
    assert out["cmdb"]["floor_reasons"]
    # medium x low = P4: P3..P5 bandında, mevcut kurallarla otomatik uygulanır
    assert out["proposed_priority"] == "P4"
    assert decision.outcome == DecisionOutcome.AUTO_APPLIED
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.impact == Impact.MEDIUM and refreshed.priority == Priority.P4


async def test_two_business_services_raise_impact_to_high_and_p2_needs_approval(session):
    database = await _two_services(session)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [database.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    before = (inc.impact, inc.priority, inc.version)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(urgency="medium")]), settings=LIVE
    )
    out = decision.output
    assert out["impact"] == "high" and out["cmdb"]["impact_floor"] == "high"
    assert len(out["cmdb"]["business_service_ids"]) == 2
    # high x medium = P2: yükseltme otomatik uygulanamaz, insan onayı gerekir
    assert out["proposed_priority"] == "P2"
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert "impact" in out["pending_fields"] and "impact" not in out["applied_fields"]
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.impact == before[0] and refreshed.priority == before[1]

    # İnsan onaylayınca uygulanır
    await service_desk.approve_decision(session, decision.id, "sef.ali")
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.impact == Impact.HIGH and refreshed.priority == Priority.P2


async def test_floor_p1_raise_needs_approval_and_pages_oncall(session):
    database = await _two_services(session)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [database.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(urgency="high", confidence=0.99)]), settings=LIVE
    )
    assert decision.output["proposed_priority"] == "P1"
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P5

    class _Notifier:
        def __init__(self):
            self.calls: list = []

        async def notify(self, event, payload):
            self.calls.append((event, payload))

    n = _Notifier()
    await session.commit()
    await notifications.send_pending(session, n)
    assert [e for e, _ in n.calls] == [service_desk.ONCALL_EVENT]


async def test_floor_never_lowers_llm_impact(session):
    ci = await _ci(session, "ERP-App", criticality=1)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [ci.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(impact="high")]), settings=SHADOW
    )
    assert decision.output["impact"] == "high"
    assert decision.output["cmdb"]["impact_floor"] == "medium"
    assert decision.output["cmdb"]["floor_applied"] is False


@pytest.mark.parametrize(
    "kw",
    [
        {"environment": CIEnvironment.TEST},
        {"criticality": 2},
        {"status": CIStatus.RETIRED},
    ],
)
async def test_no_floor_for_non_prod_noncritical_or_retired_ci(session, kw):
    data = {"criticality": 1, **kw}
    ci = await _ci(session, "ERP-App", **data)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [ci.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    decision = await service_desk.triage(session, inc, FakeLLM([_response()]), settings=SHADOW)
    assert decision.output["impact"] == "low"
    assert decision.output["cmdb"]["impact_floor"] is None
    assert decision.output["cmdb"]["floor_applied"] is False


async def test_business_services_in_test_environment_do_not_raise(session):
    database = await _two_services(session, env=CIEnvironment.TEST)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [database.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    decision = await service_desk.triage(session, inc, FakeLLM([_response()]), settings=SHADOW)
    assert decision.output["impact"] == "low"
    assert decision.output["cmdb"]["business_service_ids"] == []


async def test_floor_not_applied_to_service_requests(session):
    ci = await _ci(session, "ERP-App", criticality=1)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [ci.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(kind="service_request")]), settings=SHADOW
    )
    assert decision.output["impact"] == "low"
    assert decision.output["cmdb"]["floor_applied"] is False


async def test_ci_link_change_makes_pending_decision_stale(session):
    # Karar CMDB bağına dayanır; bağ değişirse eski öneri onaylanamaz (yeniden triage)
    ci = await _ci(session, "ERP-App", criticality=1)
    database = await _two_services(session)
    inc = await _incident(session)
    await svc.set_affected_cis(session, inc.id, [database.id], "ayse")
    inc = await svc.get_incident(session, inc.id)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(urgency="medium")]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    await svc.set_affected_cis(session, inc.id, [ci.id], "ayse")
    with pytest.raises(service_desk.StaleDecisionError):
        await service_desk.approve_decision(session, decision.id, "sef.ali")
