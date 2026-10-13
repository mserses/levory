"""Faz 3 eylem istekleri (ActionRequest): onay, red, süre aşımı, yürütme ve /actions API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _auth import AGENT as H_AGENT
from _auth import APPROVER as H_APPROVER
from _auth import dev_headers
from _tooling import (
    AGENT,
    AI,
    APPROVER,
    APPROVER2,
    CHANGE_MANAGER,
    LIVE,
    make_test_tool,
    registered,
    seed,
)
from sqlalchemy import select

from serviszeka import db
from serviszeka.api import actions as actions_api
from serviszeka.auth import CurrentUser
from serviszeka.domain.enums import IncidentStatus, RiskClass
from serviszeka.main import app
from serviszeka.models import ActionApproval, ActionRequest, AuditLog, Incident, KnowledgeArticle
from serviszeka.tools import ToolStatus, actions, execute_tool


async def _resolve_request(session, fx, actor=AI) -> str:
    r = await execute_tool(
        session, actor, "incident.resolve",
        {"incident_id": fx.incident_id, "resolution": "VPN istemcisi güncellendi"},
        reason="Kullanıcı sorunun bittiğini yazdı",
    )  # fmt: skip
    await session.commit()
    assert r.status == ToolStatus.PENDING_APPROVAL
    return r.action_request_id


async def test_single_approval_executes_r2_as_approver(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    ar = await actions.approve(session, ar_id, APPROVER, "Uygun", settings=LIVE)
    await session.commit()
    assert ar.state == "executed" and ar.executed_by == "sef.ali"
    inc = await session.get(Incident, fx.incident_id)
    assert inc.status == IncidentStatus.RESOLVED
    # Olay güncellemesinin aktörü onaylayan insandır
    actors = (
        await session.scalars(select(AuditLog.actor).where(AuditLog.action == "incident.update"))
    ).all()
    assert actors == ["sef.ali"]
    assert ar.result["status"] == "resolved"


async def test_args_display_is_masked_raw_kept(session):
    fx = await seed(session)
    r = await execute_tool(
        session, AI, "incident.resolve",
        {"incident_id": fx.incident_id, "resolution": "ali@ornek.com ile görüşüldü"},
    )  # fmt: skip
    ar = await session.get(ActionRequest, r.action_request_id)
    assert ar.args["resolution"] == "ali@ornek.com ile görüşüldü"
    assert ar.args_display["resolution"] == "[EPOSTA] ile görüşüldü"


async def test_reject_marks_rejected_and_never_runs(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    ar = await actions.reject(session, ar_id, APPROVER, "Kullanıcıya sorulmadı")
    assert ar.state == "rejected"
    with pytest.raises(actions.ActionStateError):
        await actions.approve(session, ar_id, APPROVER2)
    assert (await session.get(Incident, fx.incident_id)).status == IncidentStatus.NEW


async def test_requester_cannot_approve_or_reject_own(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx, actor=AGENT)  # insan talep etti
    for user in (
        CurrentUser("ayse", frozenset({"approver"})),
        CurrentUser(" AYSE ", frozenset({"approver", "admin"})),
    ):
        with pytest.raises(actions.ActionForbiddenError):
            await actions.approve(session, ar_id, user)
        with pytest.raises(actions.ActionForbiddenError):
            await actions.reject(session, ar_id, user)
    assert (await session.get(ActionRequest, ar_id)).state == "pending"


async def test_ai_and_unprivileged_cannot_approve(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    for user in (
        CurrentUser("ai:other", frozenset({"ai_agent"})),
        CurrentUser("ai:x", frozenset({"approver", "admin"})),
        AGENT,
        CHANGE_MANAGER,  # yalnızca değişikliğe bağlı R3'te onaylayabilir
    ):
        with pytest.raises(actions.ActionForbiddenError):
            await actions.approve(session, ar_id, user)
    assert (await session.get(ActionRequest, ar_id)).state == "pending"


async def test_expired_request_cannot_execute(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    later = datetime.now(UTC) + timedelta(hours=24, seconds=1)
    with pytest.raises(actions.ActionExpiredError):
        await actions.approve(session, ar_id, APPROVER, now=later)
    await session.commit()
    ar = await session.get(ActionRequest, ar_id)
    assert ar.state == "expired"
    assert (await session.get(Incident, fx.incident_id)).status == IncidentStatus.NEW
    with pytest.raises(actions.ActionStateError):
        await actions.approve(session, ar_id, APPROVER)


async def test_expire_due_marks_overdue(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    assert await actions.expire_due(session, datetime.now(UTC)) == 0
    assert await actions.expire_due(session, datetime.now(UTC) + timedelta(days=2)) == 1
    assert (await session.get(ActionRequest, ar_id)).state == "expired"


async def test_execution_failure_is_recorded_and_rolled_back(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    # Onaydan önce olay kapatıldı: yürütme başarısız olur, kısmi yazma kalmaz
    inc = await session.get(Incident, fx.incident_id)
    inc.status = IncidentStatus.CANCELLED
    await session.commit()
    ar = await actions.approve(session, ar_id, APPROVER)
    await session.commit()
    assert ar.state == "failed" and ar.error
    assert (await session.get(Incident, fx.incident_id)).status == IncidentStatus.CANCELLED


async def test_execution_revalidates_args(session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    ar = await session.get(ActionRequest, ar_id)
    ar.args = {"incident_id": fx.incident_id}  # kuralları atlayan yazma: zorunlu alan silindi
    await session.commit()
    ar = await actions.approve(session, ar_id, APPROVER)
    assert ar.state == "failed" and "resolution" in ar.error
    assert (await session.get(Incident, fx.incident_id)).status == IncidentStatus.NEW


async def test_execution_refuses_args_changed_after_request(session):
    """Onaylayan `args_display`'i (A olayı) onaylar; ham argüman sonradan geçerli ama farklı bir
    hedefe (B olayı) çevrilirse yürütülmez: onaylanan argüman özetiyle (`args_hash`) eşleşmeli."""
    from serviszeka.services import incidents as incident_service
    from serviszeka.services.schemas import IncidentCreate

    fx = await seed(session)
    other = await incident_service.create_incident(
        session, IncidentCreate(title="Başka olay", caller="veli"), "ayse"
    )
    await session.commit()
    ar_id = await _resolve_request(session, fx)
    ar = await session.get(ActionRequest, ar_id)
    shown = dict(ar.args_display)
    ar.args = {**ar.args, "incident_id": other.id}  # kuralları atlayan yazma, gösterim aynı kaldı
    await session.commit()
    ar = await actions.approve(session, ar_id, APPROVER, settings=LIVE)
    await session.commit()
    assert ar.state == "failed" and ar.args_display == shown
    assert (await session.get(Incident, other.id)).status == IncidentStatus.NEW
    assert (await session.get(Incident, fx.incident_id)).status == IncidentStatus.NEW


async def test_r3_requires_two_distinct_approvers(session, monkeypatch):
    calls: list = []
    with registered(monkeypatch, make_test_tool("test.critical", RiskClass.R3, calls)):
        r = await execute_tool(session, AI, "test.critical", {}, confidence=1.0, settings=LIVE)
        ar = await session.get(ActionRequest, r.action_request_id)
        assert ar.required_approvals == 2
        ar = await actions.approve(session, ar.id, APPROVER)
        assert ar.state == "pending" and calls == []
        with pytest.raises(actions.DuplicateActionApprovalError):
            await actions.approve(session, ar.id, CurrentUser("SEF.ALI ", frozenset({"approver"})))
        ar = await actions.approve(session, ar.id, APPROVER2)
        assert ar.state == "executed" and calls == [("sef.veli", calls[0][1])]
    approvals = (await session.scalars(select(ActionApproval))).all()
    assert {a.approver for a in approvals} == {"sef.ali", "sef.veli"}


async def test_risk_raised_at_execution_requires_more_approvals(session, monkeypatch):
    calls: list = []
    state = {"risk": RiskClass.R2}
    spec = make_test_tool(
        "test.escalating", RiskClass.R2, calls, risk_fn=lambda ctx, a: state["risk"]
    )
    with registered(monkeypatch, spec):
        r = await execute_tool(session, AI, "test.escalating", {}, confidence=1.0, settings=LIVE)
        state["risk"] = RiskClass.R3  # onay beklerken risk arttı (örn. grup ayrıcalıklı oldu)
        ar = await actions.approve(session, r.action_request_id, APPROVER)
        assert ar.state == "pending" and ar.risk_class == RiskClass.R3 and calls == []
        ar = await actions.approve(session, ar.id, APPROVER2)
        assert ar.state == "executed" and len(calls) == 1


async def test_r3_with_cab_approved_change_needs_one_approval(session, monkeypatch):
    fx = await seed(session)
    calls: list = []
    with registered(monkeypatch, make_test_tool("test.critical", RiskClass.R3, calls)):
        r = await execute_tool(
            session, AI, "test.critical", {}, settings=LIVE, links={"change_id": fx.change_id}
        )
        ar = await session.get(ActionRequest, r.action_request_id)
        assert ar.change_id == fx.change_id
        # Değişiklik henüz CAB onaylı değil: change_manager tek başına yetmez
        ar = await actions.approve(session, ar.id, CHANGE_MANAGER)
        assert ar.state == "pending"
        # Değişikliği CAB onaylı ve zamanlanmış hale getir (iki CAB onayı)
        from serviszeka.services import changes

        now = datetime.now(UTC)
        await changes.update_change(
            session, fx.change_id,
            changes.ChangeUpdate(planned_start=now - timedelta(minutes=5), planned_end=now + timedelta(hours=1)),
            AGENT,
        )  # fmt: skip
        mgr = CurrentUser("mudur2", frozenset({"change_manager"}))
        await changes.transition(session, fx.change_id, "assess", AGENT)
        await changes.transition(session, fx.change_id, "authorize", mgr)
        for name in ("kurul1", "kurul2"):
            await changes.record_approval(
                session, fx.change_id, CurrentUser(name, frozenset({"cab"})), "approve"
            )
        await changes.transition(session, fx.change_id, "scheduled", mgr)
        # CAB onaylı değişikliğe bağlı yeni R3 isteği: tek insan onayı yeter
        r2 = await execute_tool(
            session, AI, "test.critical", {}, settings=LIVE, links={"change_id": fx.change_id}
        )
        ar2 = await actions.approve(session, r2.action_request_id, APPROVER)
        assert ar2.state == "executed" and len(calls) == 1


# --- /actions API ----------------------------------------------------------------------------


@pytest.fixture
async def client(session) -> AsyncIterator[httpx.AsyncClient]:
    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[actions_api.get_action_settings] = lambda: LIVE
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def test_api_list_get_approve(client, session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    r = await client.get("/actions?state=pending", headers=H_APPROVER)
    assert r.status_code == 200
    items = r.json()
    assert [i["id"] for i in items] == [ar_id]
    item = items[0]
    assert item["tool"] == "incident.resolve" and item["risk_class"] == "R2"
    assert item["requested_by"] == "ai:copilot" and item["state"] == "pending"
    assert "args" not in item and item["args_display"]["incident_id"] == fx.incident_id
    assert (await client.get(f"/actions/{ar_id}", headers=H_APPROVER)).json()["id"] == ar_id
    r = await client.post(
        f"/actions/{ar_id}/approve", json={"comment": "tamam"}, headers=H_APPROVER
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "executed" and body["approvals"][0]["approver"] == "sef.ali"


async def test_api_roles_and_errors(client, session):
    fx = await seed(session)
    ar_id = await _resolve_request(session, fx)
    assert (await client.get("/actions", headers=H_AGENT)).status_code == 403
    assert (
        await client.post(f"/actions/{ar_id}/approve", json={}, headers=H_AGENT)
    ).status_code == 403
    assert (await client.get("/actions/yok", headers=H_APPROVER)).status_code == 404
    r = await client.post(f"/actions/{ar_id}/reject", json={"comment": "hayır"}, headers=H_APPROVER)
    assert r.status_code == 200 and r.json()["state"] == "rejected"
    r = await client.post(f"/actions/{ar_id}/approve", json={}, headers=H_APPROVER)
    assert r.status_code == 409
    # Self-servis kullanıcı ve yapay zekâ ajanı hiçbir eylem isteğini göremez
    for h in (dev_headers("son", "requester"), dev_headers("copilot", "ai_agent")):
        assert (await client.get("/actions", headers=h)).status_code == 403


async def test_api_kb_publish_flow(client, session):
    fx = await seed(session)
    r = await execute_tool(session, AI, "kb.publish", {"article_id": fx.draft_article_id})
    await session.commit()
    assert (await session.get(KnowledgeArticle, fx.draft_article_id)).state == "draft"
    url = f"/actions/{r.action_request_id}/approve"
    assert (await client.post(url, json={}, headers=H_APPROVER)).json()["state"] == "executed"
    await session.refresh(await session.get(KnowledgeArticle, fx.draft_article_id))
    assert (await session.get(KnowledgeArticle, fx.draft_article_id)).state == "published"
