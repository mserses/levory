"""Nöbetçi bildirimleri yalnızca başarılı commit'ten sonra gönderilir (outbox)."""

import httpx
from _auth import ADMIN, AGENT
from sqlalchemy import select

from serviszeka import audit, db, notifications
from serviszeka.agents import service_desk
from serviszeka.api import incidents as incidents_api
from serviszeka.config import Settings
from serviszeka.domain.enums import DecisionOutcome
from serviszeka.llm import FakeLLM
from serviszeka.main import app
from serviszeka.models import AIDecision, AuditLog, Incident
from serviszeka.services import incidents as svc
from serviszeka.services.schemas import IncidentCreate

LIVE = Settings(ai_shadow_mode=False, anthropic_api_key=None)
EVENT = "incident.priority_raise_pending"


class Recorder:
    def __init__(self, fail: bool = False, on_call=None):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail
        self.on_call = on_call

    async def notify(self, event, payload):
        if self.on_call is not None:
            await self.on_call()
        self.calls.append((event, payload))
        if self.fail:
            raise RuntimeError("webhook kapalı")


def _p1(**kw):
    data = {
        "kind": "incident",
        "category": "ag",
        "service": "vpn",
        "impact": "high",
        "urgency": "high",
        "assignment_group": "network_ops",
        "kb_article_ids": [],
        "confidence": 0.95,
        "rationale": "test",
    }
    data.update(kw)
    return data


async def _incident(session):
    return await svc.create_incident(
        session, IncidentCreate(title="Tüm ağ çöktü", caller="noc"), actor="noc"
    )


async def _actions(session):
    return [r.action for r in (await session.scalars(select(AuditLog).order_by(AuditLog.seq)))]


async def test_triage_stages_but_does_not_send(session):
    inc = await _incident(session)
    decision = await service_desk.triage(session, inc, FakeLLM([_p1()]), settings=LIVE)
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    staged = notifications.pending(session)
    assert [p.event for p in staged] == [EVENT]
    assert staged[0].payload["decision_id"] == decision.id
    actions = await _actions(session)
    assert "notification.sent" not in actions and "notification.failed" not in actions


async def test_send_pending_before_commit_sends_nothing(session):
    inc = await _incident(session)
    await service_desk.triage(session, inc, FakeLLM([_p1()]), settings=LIVE)
    rec = Recorder()
    assert await notifications.send_pending(session, rec) == []
    assert rec.calls == []
    assert len(notifications.pending(session)) == 1  # commit sonrası için bekler


async def test_send_after_commit_sends_and_audits_in_new_transaction(session):
    inc = await _incident(session)
    decision = await service_desk.triage(session, inc, FakeLLM([_p1()]), settings=LIVE)
    await session.commit()
    rec = Recorder()
    assert await notifications.send_pending(session, rec) == ["notification.sent"]
    assert [e for e, _ in rec.calls] == [EVENT]
    assert rec.calls[0][1]["incident_number"] == inc.number
    assert notifications.pending(session) == []
    # Denetim kaydı ayrı bir işlemde commit edildi
    async with db.get_sessionmaker()() as other:
        logs = (await other.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
        sent = [log for log in logs if log.action == "notification.sent"]
        assert sent and sent[0].entity_id == decision.id
        assert await audit.verify_chain(other)
    # İkinci çağrı tekrar göndermez
    assert await notifications.send_pending(session, rec) == []
    assert len(rec.calls) == 1


async def test_send_pending_does_not_commit_flushed_work(session):
    # Commit sonrası flush edilmiş ama commit edilmemiş iş varken gönderim yapılmaz:
    # denetim commit'i o işi de istemeden kalıcı kılardı
    inc = await _incident(session)
    await service_desk.triage(session, inc, FakeLLM([_p1()]), settings=LIVE)
    await session.commit()
    other_id = (await _incident(session)).id  # flush edildi, commit edilmedi
    rec = Recorder()
    assert await notifications.send_pending(session, rec) == []
    assert rec.calls == []
    await session.rollback()
    assert await session.get(Incident, other_id) is None
    # Bekleyen (commit edilmiş) bildirim kaybolmaz
    assert await notifications.send_pending(session, rec) == ["notification.sent"]


async def test_no_transaction_held_while_sending(session):
    inc = await _incident(session)
    await service_desk.triage(session, inc, FakeLLM([_p1()]), settings=LIVE)
    await session.commit()
    seen: list[bool] = []

    async def check():
        seen.append(session.in_transaction())

    await notifications.send_pending(session, Recorder(on_call=check))
    assert seen == [False]


async def test_rollback_discards_staged_notifications(session):
    inc = await _incident(session)
    await session.commit()
    await service_desk.triage(session, inc, FakeLLM([_p1()]), settings=LIVE)
    await session.rollback()  # karar kaydedilmedi
    rec = Recorder()
    assert await notifications.send_pending(session, rec) == []
    assert rec.calls == []
    assert notifications.pending(session) == []
    assert (await session.scalars(select(AIDecision))).all() == []


async def test_send_failure_logged_and_audited_never_raises(session, caplog):
    inc = await _incident(session)
    decision = await service_desk.triage(session, inc, FakeLLM([_p1()]), settings=LIVE)
    await session.commit()
    rec = Recorder(fail=True)
    assert await notifications.send_pending(session, rec) == ["notification.failed"]
    assert "Nöbetçi bildirimi başarısız" in caplog.text
    async with db.get_sessionmaker()() as other:
        logs = (await other.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
        failed = [log for log in logs if log.action == "notification.failed"]
        assert failed and failed[0].entity_id == decision.id
        assert "webhook kapalı" in failed[0].details["error"]
        assert await audit.verify_chain(other)


async def test_low_confidence_raise_still_staged(session):
    inc = await _incident(session)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_p1(confidence=0.3)]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.ESCALATED
    assert [p.event for p in notifications.pending(session)] == [EVENT]


def _client_ctx(session, notifier, llm):
    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: llm
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: LIVE
    app.dependency_overrides[incidents_api.get_oncall_notifier] = lambda: notifier
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=AGENT
    )


async def test_api_notifies_only_after_decision_committed(session):
    seen: list[int] = []

    async def count_committed():
        # Bildirim anında karar başka bir oturumdan görülebilir olmalı (commit edilmiş)
        async with db.get_sessionmaker()() as other:
            seen.append(len((await other.scalars(select(AIDecision))).all()))

    rec = Recorder(on_call=count_committed)
    try:
        async with _client_ctx(session, rec, FakeLLM([_p1()])) as client:
            inc = (await client.post("/incidents", json={"title": "Ağ", "caller": "n"})).json()
            r = await client.post(f"/incidents/{inc['id']}/triage")
            assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
    assert [e for e, _ in rec.calls] == [EVENT]
    assert seen == [1]
    async with db.get_sessionmaker()() as s:
        assert "notification.sent" in await _actions(s)


async def test_api_commit_failure_does_not_page(session, monkeypatch):
    rec = Recorder()
    from sqlalchemy.ext.asyncio import AsyncSession

    real_commit = AsyncSession.commit
    state = {"fail": False}

    async def flaky_commit(self):
        if state["fail"]:
            await self.rollback()
            raise RuntimeError("commit başarısız")
        return await real_commit(self)

    try:
        async with _client_ctx(session, rec, FakeLLM([_p1()])) as client:
            inc = (await client.post("/incidents", json={"title": "Ağ", "caller": "n"})).json()
            monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
            state["fail"] = True
            try:
                await client.post(f"/incidents/{inc['id']}/triage")
            except RuntimeError:
                pass  # ASGITransport istisnayı yeniden fırlatabilir
            state["fail"] = False
    finally:
        app.dependency_overrides.clear()
    assert rec.calls == []


async def test_api_notification_failure_does_not_break_triage(session):
    rec = Recorder(fail=True)
    try:
        async with _client_ctx(session, rec, FakeLLM([_p1()])) as client:
            inc = (await client.post("/incidents", json={"title": "Ağ", "caller": "n"})).json()
            r = await client.post(f"/incidents/{inc['id']}/triage")
            assert r.status_code == 200, r.text
            assert r.json()["outcome"] == "needs_approval"
            r = await client.get("/audit/verify", headers=ADMIN)
            assert r.json() == {"valid": True}
    finally:
        app.dependency_overrides.clear()
    async with db.get_sessionmaker()() as s:
        actions = await _actions(s)
        assert "notification.failed" in actions and "ai_decision.triage" in actions
