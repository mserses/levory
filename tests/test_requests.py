"""Hizmet talepleri: karşılama motoru, iş onayı, eylem isteğiyle devam, insansız ölçüm, araçlar, API.

Dış sistem yok: sahte dizin (`InMemoryDirectory.demo()`, fikstür `directory`).
"""

from __future__ import annotations

import httpx
import pytest
from _auth import dev_headers
from _requests import (
    ALI,
    APPROVER,
    APPROVER2,
    DESK,
    LIVE_REQ,
    MEHMET,
    SHADOW_REQ,
    ZEYNEP,
    seeded_item,
    writes,
)
from sqlalchemy import select

from serviszeka.api.requests import get_request_settings
from serviszeka.auth import CurrentUser
from serviszeka.main import app
from serviszeka.models import ActionRequest, AIDecision, AuditLog, ToolInvocation
from serviszeka.services import requests
from serviszeka.tools import ToolStatus, actions, execute_tool
from serviszeka.tools.spec import ToolValidationError

FULFILMENT = "ai:fulfilment"


async def _create(session, key, requester, opener, form=None, settings=LIVE_REQ):
    item = await seeded_item(session, key)
    return await requests.create_request(
        session, item, requester=requester.username if isinstance(requester, CurrentUser)
        else requester, opened_by=opener, form_values=form or {}, settings=settings,
    )


# --- karşılama motoru ---------------------------------------------------------------------------------


async def test_self_service_unlock_is_touchless(session, directory):
    req = await _create(session, "account_unlock", ALI, ALI)
    assert req.number == "REQ0000001"
    assert req.state == "fulfilled" and req.touchless is True and req.touchless_reason is None
    assert req.human_approvals == 0 and req.human_edits == 0 and req.fulfilled_at is not None
    assert [s["status"] for s in req.step_results] == ["executed"] * len(req.plan)
    assert all(s["actor"] == FULFILMENT for s in req.step_results)
    assert [w[0] for w in writes(directory, "unlock_account")] == ["unlock_account"]
    # Her R1 yazma adımı politika + AIDecision ile geçti
    decisions = (await session.scalars(select(AIDecision))).all()
    assert decisions and {d.outcome for d in decisions} == {"auto_applied"}
    assert await requests.linked_actions(session, req.id) == []
    second = await _create(session, "account_unlock", MEHMET, MEHMET)
    assert second.number == "REQ0000002"


async def test_password_reset_never_exposes_temporary_password(session, directory):
    req = await _create(session, "password_reset", ALI, ALI)
    assert req.state == "fulfilled" and req.touchless is True
    (msg,) = [m for m in directory.messages if m.sensitive]
    assert msg.target == "ali@kisisel.ornek"
    password = msg.text.split("Geçici parolanız: ", 1)[1].split("\n", 1)[0]
    assert len(password) >= 12
    blobs = [str(req.step_results), str(req.touches)]
    blobs += [str(a.details) for a in (await session.scalars(select(AuditLog))).all()]
    blobs += [f"{d.input_summary}{d.output}{d.rationale}"
              for d in (await session.scalars(select(AIDecision))).all()]
    blobs += [str(i.result) for i in (await session.scalars(select(ToolInvocation))).all()]
    assert all(password not in b for b in blobs)


async def test_shadow_mode_r1_step_waits_for_human(session, directory):
    req = await _create(session, "account_unlock", ALI, ALI, settings=SHADOW_REQ)
    assert req.state == "fulfilling" and req.waiting_action_id
    assert writes(directory, "unlock_account") == []
    ar = await actions.approve(session, req.waiting_action_id, APPROVER, settings=SHADOW_REQ)
    assert ar.state == "executed"
    await session.refresh(req)
    # Gölge modda sonraki R1 adım (bildirim) da onay bekler: insansız değildir
    while req.state == "fulfilling" and req.waiting_action_id:
        await actions.approve(session, req.waiting_action_id, APPROVER, settings=SHADOW_REQ)
        await session.refresh(req)
    assert req.state == "fulfilled" and req.touchless is False
    assert "sef.ali" in req.touchless_reason
    assert len(writes(directory, "unlock_account")) == 1


async def test_manager_approval_then_r2_step_resumes_after_action(session, directory):
    req = await _create(session, "shared_mailbox_access", ALI, ALI, {"mailbox": "muhasebe"})
    assert req.state == "awaiting_approval" and req.approval_state == "pending"
    assert req.approver.startswith("zeynep")
    # Talep eden, yönetici olmayan ve yapay zekâ iş onayı veremez
    for user in (ALI, MEHMET, CurrentUser("ai:x", frozenset({"admin"}))):
        with pytest.raises(requests.RequestForbiddenError):
            await requests.decide(session, req.id, user, "approve", settings=LIVE_REQ)
    with pytest.raises(requests.RequestVersionConflictError):
        await requests.decide(session, req.id, ZEYNEP, "approve", expected_version=99)
    req = await requests.decide(session, req.id, ZEYNEP, "approve", "uygun", settings=LIVE_REQ)
    assert req.state == "fulfilling" and req.human_approvals == 1
    (ar,) = await requests.linked_actions(session, req.id)
    assert ar.risk_class == "R2" and ar.requested_by == FULFILMENT and ar.state == "pending"
    assert writes(directory, "grant_shared_access") == []
    # İnsan onaylar; yürütme sonrası karşılama kaldığı yerden sürer
    await actions.approve(session, ar.id, APPROVER, settings=LIVE_REQ)
    await session.refresh(req)
    assert req.state == "fulfilled" and req.touchless is False
    assert "iş onayı: zeynep" in req.touchless_reason and "sef.ali" in req.touchless_reason
    assert len(writes(directory, "grant_shared_access")) == 1
    assert req.step_results[0]["action_request_id"] == ar.id


async def test_business_rejection_closes_without_writes(session, directory):
    req = await _create(session, "shared_mailbox_access", ALI, ALI, {"mailbox": "muhasebe"})
    req = await requests.decide(session, req.id, ZEYNEP, "reject", "gerek yok")
    assert req.state == "rejected" and req.touchless is False and req.closed_at
    assert directory.writes == []
    with pytest.raises(requests.RequestStateError):
        await requests.decide(session, req.id, ZEYNEP, "approve")


async def test_rejected_action_request_rejects_service_request(session, directory):
    req = await _create(session, "mfa_reset", MEHMET, MEHMET)
    (ar,) = await requests.linked_actions(session, req.id)
    await actions.reject(session, ar.id, APPROVER, "kimlik şüpheli")
    await session.refresh(req)
    assert req.state == "rejected" and req.waiting_action_id is None
    assert writes(directory, "reset_mfa") == []


async def test_r3_privileged_group_needs_two_humans(session, directory):
    req = await _create(session, "privileged_group_access", ALI, ALI,
                        {"group": "sg-domain-admins", "justification": "Sunucu bakımı için geçici yetki"})
    req = await requests.decide(session, req.id, ZEYNEP, "approve", settings=LIVE_REQ)
    (ar,) = await requests.linked_actions(session, req.id)
    assert ar.risk_class == "R3" and ar.required_approvals == 2
    await actions.approve(session, ar.id, APPROVER, settings=LIVE_REQ)
    await session.refresh(req)
    assert req.state == "fulfilling" and writes(directory, "add_to_group") == []
    await actions.approve(session, ar.id, APPROVER2, settings=LIVE_REQ)
    await session.refresh(req)
    assert req.state == "fulfilled" and req.human_approvals == 3 and req.touchless is False
    assert len(writes(directory, "add_to_group")) == 1


async def test_failed_step_retry_is_human_touch(session, directory):
    req = await _create(session, "m365_license", ALI, ALI, {"sku": "POWER_BI_PRO"})
    assert req.state == "failed" and "boş lisans yok" in req.error and req.touchless is False
    directory.add_sku("sku-power-bi-pro", "POWER_BI_PRO", enabled=15, consumed=10)
    with pytest.raises(requests.RequestVersionConflictError):
        await requests.retry(session, req.id, DESK, expected_version=1)
    req = await requests.retry(session, req.id, DESK, settings=LIVE_REQ)
    assert req.state == "fulfilled" and req.attempt == 2 and req.touchless is False
    assert "yeniden deneme" in req.touchless_reason


async def test_form_validation_and_edit_counts_as_touch(session, directory):
    with pytest.raises(requests.RequestValidationError):
        await _create(session, "standard_software_group", ALI, ALI, {})
    with pytest.raises(requests.RequestValidationError):
        await _create(session, "account_unlock", "ai:bot", ALI)
    req = await _create(session, "shared_mailbox_access", ALI, ALI, {"mailbox": "muhasebe"})
    req = await requests.update_form(session, req.id, DESK, {"mailbox": "destek"})
    assert req.form_values["mailbox"] == "destek" and req.human_edits == 1


async def test_cancel_voids_pending_action_and_is_not_measured(session, directory):
    req = await _create(session, "mfa_reset", MEHMET, MEHMET)
    (ar,) = await requests.linked_actions(session, req.id)
    await requests.cancel(session, req.id, MEHMET, "gerek kalmadı")
    await session.refresh(ar)
    assert req.state == "cancelled" and ar.state == "rejected"
    with pytest.raises(actions.ActionError):
        await actions.approve(session, ar.id, APPROVER, settings=LIVE_REQ)
    assert writes(directory, "reset_mfa") == []
    m = await requests.touchless_metrics(session)
    assert m.all_closed == 0


async def test_touchless_metrics(session, directory):
    await _create(session, "account_unlock", ALI, ALI)  # insansız
    await _create(session, "password_reset", ALI, DESK)  # ajan adına: R2 bekler (açık)
    await _create(session, "vpn_access", MEHMET, MEHMET, {"justification": "Evden"})  # insansız
    await _create(session, "m365_license", ALI, ALI, {"sku": "POWER_BI_PRO"})  # başarısız
    m = await requests.touchless_metrics(session)
    assert (m.standard_closed, m.standard_touchless) == (3, 2)
    assert m.meets_gate and round(m.standard_touchless_rate, 2) == 0.67


# --- araçlar --------------------------------------------------------------------------------------------


AI = CurrentUser("ai:servis-masasi", frozenset({"ai_agent"}))


async def test_catalog_and_request_tools(session, directory):
    await seeded_item(session, "account_unlock")
    r = await execute_tool(session, AI, "catalog.list_items", {"standard": True})
    assert r.status == ToolStatus.EXECUTED
    keys = {i["key"] for i in r.result["items"]}
    assert "account_unlock" in keys and "new_employee_account" not in keys
    r = await execute_tool(session, AI, "catalog.get_item", {"item": "vpn_access"})
    assert r.status == ToolStatus.EXECUTED and r.result["key"] == "vpn_access"
    # request.create R1: düşük güvende onaya düşer, hiçbir şey açılmaz
    r = await execute_tool(session, AI, "request.create",
                           {"item": "account_unlock", "requester": "mehmet"},
                           confidence=0.1, settings=LIVE_REQ)
    assert r.status == ToolStatus.PENDING_APPROVAL
    assert await requests.list_requests(session) == []
    r = await execute_tool(session, AI, "request.create",
                           {"item": "account_unlock", "requester": "mehmet"},
                           confidence=0.95, settings=LIVE_REQ)
    assert r.status == ToolStatus.EXECUTED and r.result["state"] == "fulfilled"
    rid = r.result["id"]
    got = await execute_tool(session, AI, "request.get", {"request_id": rid})
    assert got.status == ToolStatus.EXECUTED and got.result["number"] == r.result["number"]
    with pytest.raises(ToolValidationError):
        await execute_tool(session, AI, "request.get", {"request_id": rid, "x": 1})
    # Yapay zekânın açtığı talepte kimlik doğrulaması geçmez: şifre sıfırlama R2'ye çıkar
    r = await execute_tool(session, AI, "request.create",
                           {"item": "password_reset", "requester": "mehmet"},
                           confidence=0.95, settings=LIVE_REQ)
    assert r.result["state"] == "fulfilling" and r.result["actions"][0]["risk_class"] == "R2"


async def test_identity_read_and_verify_tools(session, directory):
    r = await execute_tool(session, AI, "identity.get_user", {"user": "ali"})
    assert r.status == ToolStatus.EXECUTED
    assert "ali" in str(r.result)
    r = await execute_tool(session, AI, "license.check_available", {"sku": "SPE_E3"})
    assert r.status == ToolStatus.EXECUTED and r.result["available"] == 3
    req = await _create(session, "password_reset", ALI, DESK)  # ajan açtı
    r = await execute_tool(session, AI, "identity.verify_requester",
                           {"request_id": req.id, "user": "ali"})
    assert r.status == ToolStatus.EXECUTED and r.result["verified"] is False


async def test_r1_identity_tools_and_dry_run(session, directory):
    r = await execute_tool(session, AI, "notify.post_to_channel",
                           {"channel": "servis-masasi", "message": "ali@ornek.com bekliyor"},
                           confidence=0.9, settings=LIVE_REQ, dry_run=True)
    assert r.status == ToolStatus.DRY_RUN and directory.messages == []
    r = await execute_tool(session, AI, "notify.post_to_channel",
                           {"channel": "servis-masasi", "message": "ali@ornek.com bekliyor"},
                           confidence=0.9, settings=LIVE_REQ)
    assert r.status == ToolStatus.EXECUTED
    assert "ali@ornek.com" not in directory.messages[-1].text
    r = await execute_tool(session, AI, "license.assign", {"user": "mehmet", "sku": "SPE_E3"},
                           confidence=0.9, settings=LIVE_REQ, idempotency_key="lic-1")
    assert r.status == ToolStatus.EXECUTED
    again = await execute_tool(session, AI, "license.assign", {"user": "mehmet", "sku": "SPE_E3"},
                               confidence=0.9, settings=LIVE_REQ, idempotency_key="lic-1")
    assert again.status == ToolStatus.EXECUTED and len(writes(directory, "assign_license")) == 1
    r = await execute_tool(session, AI, "identity.add_to_group",
                           {"user": "mehmet", "group": "sg-vpn-kullanicilari"},
                           confidence=0.9, settings=LIVE_REQ)
    assert r.status == ToolStatus.EXECUTED and r.risk_class.value == "R1"


@pytest.mark.parametrize(
    ("tool", "args", "risk"),
    [
        ("identity.reset_mfa", {"user": "ali"}, "R2"),
        ("identity.remove_from_group", {"user": "ali", "group": "sg-vpn-kullanicilari"}, "R2"),
        ("identity.revoke_sessions", {"user": "ali"}, "R2"),
        ("identity.disable_user", {"user": "ali", "reason_code": "offboarding"}, "R3"),
        ("mailbox.grant_shared_access", {"mailbox": "muhasebe", "user": "ali"}, "R2"),
        ("identity.add_to_group", {"user": "ali", "group": "sg-domain-admins"}, "R3"),
        ("identity.add_to_group", {"user": "ali", "group": "sg-finans-paylasim"}, "R3"),
        ("identity.reset_password", {"user": "ali"}, "R2"),
    ],
)
async def test_risky_identity_tools_never_run_for_ai(session, directory, tool, args, risk):
    r = await execute_tool(session, AI, tool, args, confidence=1.0, settings=LIVE_REQ)
    assert r.status == ToolStatus.PENDING_APPROVAL and r.risk_class.value == risk
    assert directory.writes == [] and directory.messages == []
    ar = await session.get(ActionRequest, r.action_request_id)
    assert ar.required_approvals == (2 if risk == "R3" else 1)


# --- API ----------------------------------------------------------------------------------------------


@pytest.fixture
async def api(session, directory):
    app.dependency_overrides[get_request_settings] = lambda: LIVE_REQ
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_request_settings, None)


async def test_requests_api_self_service_and_visibility(api, session):
    await seeded_item(session, "account_unlock")
    await session.commit()
    ali, mehmet = dev_headers("ali", "requester"), dev_headers("mehmet", "requester")
    r = await api.post("/requests", json={"item": "account_unlock"}, headers=ali)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "fulfilled" and body["touchless"] is True
    assert body["requester"] == "ali" and body["number"] == "REQ0000001"
    # Başkası adına yalnızca agent
    r = await api.post("/requests", json={"item": "account_unlock", "requester": "ali"},
                       headers=mehmet)
    assert r.status_code == 403
    r = await api.post("/requests", json={"item": "account_unlock", "requester": "mehmet"},
                       headers=dev_headers("ayse", "agent"))
    assert r.status_code == 201
    assert (await api.post("/requests", json={"item": "yok"}, headers=ali)).status_code == 404
    r = await api.post("/requests", json={"item": "standard_software_group"}, headers=ali)
    assert r.status_code == 422
    # Görünürlük: requester yalnızca kendini görür; başkasınınki 404
    assert [x["requester"] for x in (await api.get("/requests", headers=mehmet)).json()] == ["mehmet"]
    assert (await api.get(f"/requests/{body['id']}", headers=mehmet)).status_code == 404
    assert (await api.get(f"/requests/{body['number']}", headers=ali)).status_code == 200
    r = await api.get("/requests", headers=dev_headers("ayse", "agent"))
    assert len(r.json()) == 2
    r = await api.get("/requests/metrics", headers=dev_headers("ayse", "agent"))
    assert r.status_code == 200 and r.json()["standard_closed"] == 2
    assert r.json()["meets_gate"] is True
    # Yapay zekâ belirteci insan REST uçlarını kullanamaz
    r = await api.get("/requests", headers=dev_headers("bot", "ai_agent"))
    assert r.status_code == 403


async def test_requests_api_business_approval_and_cancel(api, session, directory):
    await seeded_item(session, "shared_mailbox_access")
    await session.commit()
    ali, zeynep = dev_headers("ali", "requester"), dev_headers("zeynep", "requester")
    r = await api.post("/requests", json={"item": "shared_mailbox_access",
                                          "form_values": {"mailbox": "muhasebe"}}, headers=ali)
    req = r.json()
    assert req["state"] == "awaiting_approval"
    # Onaylayacak yönetici talebi görür ve listesinde bulur; talep eden kendini onaylayamaz
    r = await api.get("/requests", params={"awaiting_my_approval": True}, headers=zeynep)
    assert [x["id"] for x in r.json()] == [req["id"]]
    assert (await api.post(f"/requests/{req['id']}/approve", headers=ali)).status_code == 403
    r = await api.post(f"/requests/{req['id']}/approve", json={"comment": "uygun"}, headers=zeynep)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "fulfilling" and body["actions"][0]["risk_class"] == "R2"
    assert writes(directory, "grant_shared_access") == []
    # Başkası iptal edemez; talep eden eder, bekleyen eylem isteği düşer
    other = dev_headers("mehmet", "requester")
    assert (await api.post(f"/requests/{req['id']}/cancel", headers=other)).status_code == 404
    r = await api.post(f"/requests/{req['id']}/cancel", json={"note": "vazgeçtim"}, headers=ali)
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert r.json()["actions"][0]["state"] == "rejected"
    # Ajan işlemleri: requester düzenleyemez
    r = await api.patch(f"/requests/{req['id']}", json={"form_values": {}}, headers=ali)
    assert r.status_code == 403
