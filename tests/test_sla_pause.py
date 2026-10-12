"""ON_HOLD sırasında SLA saatinin durdurulması (bekleme nedeni) testleri."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from serviszeka import audit, db
from serviszeka.domain.enums import Impact, IncidentStatus, OnHoldReason, Urgency
from serviszeka.main import app
from serviszeka.models import AuditLog
from serviszeka.services import incidents as svc
from serviszeka.services import sla
from serviszeka.services.schemas import IncidentCreate, IncidentRead, IncidentUpdate

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def at(self, **kw) -> datetime:
        self.now = T0 + timedelta(**kw)
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(svc, "_now", c)
    return c


async def _p2(session):
    # P2: yanıt 30 dk, çözüm 8 saat
    return await svc.create_incident(
        session,
        IncidentCreate(title="VPN", caller="ayse", impact=Impact.HIGH, urgency=Urgency.MEDIUM),
        "ayse",
    )


async def _upd(session, inc, **kw):
    return await svc.update_incident(session, inc.id, IncidentUpdate(**kw), "ali")


async def _actions(session):
    return [(log.action, log.details) for log in (await session.scalars(
        select(AuditLog).order_by(AuditLog.seq)
    )).all()]


def test_on_hold_reason_enum():
    assert {r.value for r in OnHoldReason} == {
        "awaiting_caller", "awaiting_vendor", "awaiting_change", "internal",
    }
    assert sla.PAUSING_HOLD_REASONS == frozenset(
        {OnHoldReason.AWAITING_CALLER, OnHoldReason.AWAITING_VENDOR}
    )


async def test_on_hold_requires_reason(session, clock):
    inc = await _p2(session)
    with pytest.raises(svc.InvalidTransitionError):
        await _upd(session, inc, status="on_hold")
    with pytest.raises(svc.InvalidTransitionError):
        await _upd(session, inc, status="on_hold", on_hold_reason=None)
    assert inc.status == IncidentStatus.NEW and inc.version == 1


async def test_reason_only_allowed_on_hold(session, clock):
    inc = await _p2(session)
    with pytest.raises(svc.InvalidTransitionError):
        await _upd(session, inc, on_hold_reason="awaiting_caller")


async def test_cannot_clear_reason_while_on_hold(session, clock):
    inc = await _p2(session)
    await _upd(session, inc, status="on_hold", on_hold_reason="internal")
    with pytest.raises(svc.InvalidTransitionError):
        await _upd(session, inc, on_hold_reason=None)


async def test_pausing_hold_stops_clock_and_shifts_due_dates(session, clock):
    inc = await _p2(session)
    await _upd(session, inc, status="in_progress")  # yanıt karşılandı (T0)
    response_due = sla.ensure_utc(inc.response_due_at)
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_caller")
    assert inc.on_hold_reason == OnHoldReason.AWAITING_CALLER
    assert sla.ensure_utc(inc.sla_paused_at) == T0 + timedelta(hours=1)

    clock.at(hours=3)
    await _upd(session, inc, status="in_progress")
    assert inc.sla_paused_at is None and inc.on_hold_reason is None
    assert inc.sla_paused_seconds == 2 * 3600
    # Çözüm vadesi 2 saat ileri; yanıt zaten karşılandığı için yanıt vadesi sabit
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=10)
    assert sla.ensure_utc(inc.response_due_at) == response_due

    acts = await _actions(session)
    pause = [d for a, d in acts if a == "incident.sla_pause"]
    resume = [d for a, d in acts if a == "incident.sla_resume"]
    assert pause == [{"reason": "awaiting_caller", "paused_at": (T0 + timedelta(hours=1)).isoformat()}]
    assert resume[0]["reason"] == "awaiting_caller"
    assert resume[0]["duration_seconds"] == 7200
    assert resume[0]["total_paused_seconds"] == 7200
    assert await audit.verify_chain(session)


async def test_unanswered_response_due_is_shifted(session, clock):
    inc = await _p2(session)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_vendor")
    # NEW -> ON_HOLD ilk yanıt sayılır; yanıtlanmamış senaryo için geri alınır
    inc.responded_at = None
    await session.flush()
    clock.at(minutes=20)
    await _upd(session, inc, status="assigned")
    assert sla.ensure_utc(inc.response_due_at) == T0 + timedelta(minutes=50)
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=8, minutes=20)


@pytest.mark.parametrize("reason", ["awaiting_change", "internal"])
async def test_non_pausing_hold_keeps_clock(session, clock, reason):
    inc = await _p2(session)
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason=reason)
    assert inc.sla_paused_at is None
    clock.at(hours=3)
    await _upd(session, inc, status="in_progress")
    assert inc.sla_paused_seconds == 0
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=8)
    actions = [a for a, _ in await _actions(session)]
    assert "incident.sla_pause" not in actions and "incident.sla_resume" not in actions


async def test_reason_change_while_on_hold(session, clock):
    inc = await _p2(session)
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="internal")
    assert inc.sla_paused_at is None
    # Durdurmayan -> durduran: saat durur
    clock.at(hours=2)
    await _upd(session, inc, on_hold_reason="awaiting_vendor")
    assert sla.ensure_utc(inc.sla_paused_at) == T0 + timedelta(hours=2)
    # Durduran -> durduran: saat durmaya devam eder, yeni başlangıç yok
    clock.at(hours=3)
    await _upd(session, inc, on_hold_reason="awaiting_caller")
    assert sla.ensure_utc(inc.sla_paused_at) == T0 + timedelta(hours=2)
    # Durduran -> durdurmayan: saat yeniden işler
    clock.at(hours=5)
    await _upd(session, inc, on_hold_reason="awaiting_change")
    assert inc.status == IncidentStatus.ON_HOLD and inc.sla_paused_at is None
    assert inc.sla_paused_seconds == 3 * 3600
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=11)
    acts = await _actions(session)
    assert [a for a, _ in acts if a.startswith("incident.sla_")] == [
        "incident.sla_pause", "incident.sla_resume",
    ]
    resume = next(d for a, d in acts if a == "incident.sla_resume")
    assert resume["reason"] == "awaiting_caller" and resume["duration_seconds"] == 3 * 3600


async def test_resolve_directly_from_pausing_hold(session, clock):
    inc = await _p2(session)
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_caller")
    clock.at(hours=10)  # bekleme olmasa çözüm vadesi (8s) geçmişti
    await _upd(session, inc, status="resolved", resolution="Kullanıcı doğruladı")
    assert inc.sla_paused_at is None and inc.on_hold_reason is None
    assert inc.sla_paused_seconds == 9 * 3600
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=17)
    state = sla.DEFAULT_POLICY.state(
        start=T0, due=inc.resolve_due_at, now=clock.now, completed_at=inc.resolved_at
    )
    assert state == sla.SLAState.MET
    assert any(a == "incident.sla_resume" for a, _ in await _actions(session))


async def test_naive_paused_at_from_sqlite(session, clock):
    inc = await _p2(session)
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_vendor")
    await session.commit()
    inc_id = inc.id
    session.expire_all()
    inc = await svc.get_incident(session, inc_id)
    # SQLite saat dilimini düşürür: yeniden yüklenen değer saat dilimsizdir
    assert inc.sla_paused_at.tzinfo is None
    clock.at(hours=2)
    await _upd(session, inc, status="in_progress")
    assert inc.sla_paused_seconds == 3600
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=9)


async def test_check_breaches_skips_paused_incident(session, clock):
    inc = await _p2(session)
    await _upd(session, inc, status="in_progress")
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_caller")
    await session.commit()
    # Bekleme olmasa ihlal (9s > 8s) ve risk raporlanırdı
    assert await sla.check_breaches(session, T0 + timedelta(hours=9)) == []
    assert await sla.check_breaches(session, T0 + timedelta(hours=7)) == []


async def test_at_risk_uses_effective_elapsed(session, clock):
    inc = await _p2(session)
    await _upd(session, inc, status="in_progress")
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_vendor")
    clock.at(hours=21)
    await _upd(session, inc, status="in_progress")
    await session.commit()
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=28)
    # Etkin geçen süre 24-20 = 4s / 8s = %50 -> uyarı yok (ham oran 24/28 = %86 olurdu)
    assert await sla.check_breaches(session, T0 + timedelta(hours=24)) == []
    # 27-20 = 7s / 8s = %87.5 -> risk
    alerts = await sla.check_breaches(session, T0 + timedelta(hours=27))
    assert [(a.metric, a.state, a.elapsed_ratio) for a in alerts] == [
        (sla.SLAMetric.RESOLVE, sla.SLAState.AT_RISK, 0.875)
    ]
    # Ötelenmiş vade geçince ihlal
    alerts = await sla.check_breaches(session, T0 + timedelta(hours=28, minutes=1))
    assert alerts[0].state == sla.SLAState.BREACHED


def test_policy_elapsed_ratio_with_pause():
    p = sla.DEFAULT_POLICY
    due = T0 + timedelta(hours=10)
    paused = timedelta(hours=2)
    assert p.elapsed_ratio(T0, due, T0 + timedelta(hours=6), paused=paused) == 0.5
    assert p.state(start=T0, due=due, now=T0 + timedelta(hours=8), paused=paused) == (
        sla.SLAState.AT_RISK
    )
    assert p.state(start=T0, due=due, now=T0 + timedelta(hours=7), paused=paused) == (
        sla.SLAState.ON_TRACK
    )


async def test_priority_change_keeps_paused_offset(session, clock):
    inc = await _p2(session)
    await _upd(session, inc, status="in_progress")
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_caller")
    clock.at(hours=2)
    await _upd(session, inc, status="in_progress")
    # P2 -> P1 (çözüm 4s): yeni vade biriken bekleme süresiyle ötelenir
    await _upd(session, inc, urgency="high")
    assert sla.ensure_utc(inc.resolve_due_at) == T0 + timedelta(hours=5)


async def test_read_schema_exposes_pause_fields(session, clock):
    inc = await _p2(session)
    clock.at(hours=1)
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_caller")
    data = IncidentRead.model_validate(inc)
    assert data.on_hold_reason == OnHoldReason.AWAITING_CALLER
    assert data.sla_paused_at == T0 + timedelta(hours=1)
    assert data.sla_paused_seconds == 0


@pytest.fixture
async def client(session) -> AsyncIterator[httpx.AsyncClient]:
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_api_on_hold_without_reason_is_422(client):
    r = await client.post("/incidents", json={"title": "VPN", "caller": "ayse"})
    assert r.status_code == 201, r.text
    url = f"/incidents/{r.json()['id']}"
    r = await client.patch(url, json={"status": "on_hold"})
    assert r.status_code == 422
    assert "on_hold_reason" in r.json()["detail"]
    r = await client.patch(url, json={"status": "on_hold", "on_hold_reason": "awaiting_caller"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["on_hold_reason"] == "awaiting_caller" and body["sla_paused_at"] is not None
    r = await client.patch(url, json={"status": "on_hold", "on_hold_reason": "bilinmiyor"})
    assert r.status_code == 422


async def test_breach_before_pause_still_reported_while_paused(session, clock):
    # Bekleme, beklemeden önce gerçekleşmiş ihlali gizleyemez (saat ihlal anında durmaz)
    inc = await _p2(session)
    await _upd(session, inc, status="in_progress")
    clock.at(hours=9)  # çözüm vadesi (8s) geçti
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_caller")
    await session.commit()
    alerts = await sla.check_breaches(session, T0 + timedelta(hours=12))
    assert [(a.metric, a.state, a.due_at) for a in alerts] == [
        (sla.SLAMetric.RESOLVE, sla.SLAState.BREACHED, T0 + timedelta(hours=8))
    ]


async def test_at_risk_before_pause_not_reported_while_paused(session, clock):
    # Duran saatte risk uyarısı üretilmez (yalnızca gerçekleşmiş ihlal raporlanır)
    inc = await _p2(session)
    await _upd(session, inc, status="in_progress")
    clock.at(hours=7)  # %87.5 -> risk
    await _upd(session, inc, status="on_hold", on_hold_reason="awaiting_vendor")
    await session.commit()
    assert await sla.check_breaches(session, T0 + timedelta(hours=20)) == []
