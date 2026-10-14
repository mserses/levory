"""Arka plan zamanlayıcısı: DB kirası (tek kopya), işler sahte saatle doğrudan sürülür."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _requests import ALI, LIVE_REQ, seeded_item, writes
from sqlalchemy import select

from serviszeka import db, notifications, scheduler
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import IncidentStatus, NotificationState, Priority
from serviszeka.models import (
    ActionRequest,
    AuditLog,
    Incident,
    NotificationOutbox,
    SchedulerLease,
    ServiceRequest,
)
from serviszeka.services import incidents as incident_service
from serviszeka.services import requests as request_service
from serviszeka.services.schemas import IncidentCreate

T0 = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
SETTINGS = Settings(
    notification_max_attempts=3, notification_backoff_seconds=10, request_approval_ttl_hours=48
)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def notify(self, event, payload, *, idempotency_key=None):
        self.calls.append((event, payload))
        if self.fail:
            raise RuntimeError("nöbetçi kapalı")


def _scheduler(clock, rec=None, *, jobs=scheduler.DEFAULT_JOBS, owner="kopya-1", settings=SETTINGS):
    rec = rec or Recorder()
    return scheduler.Scheduler(
        settings, clock=clock, notifier_factory=lambda s: rec, jobs=jobs, owner=owner
    )


async def _audit_actions():
    async with db.get_sessionmaker()() as s:
        return [r.action for r in (await s.scalars(select(AuditLog).order_by(AuditLog.seq)))]


# --- kira ----------------------------------------------------------------------------------------------


async def test_only_one_replica_runs_a_job_per_interval(session):
    runs: list[str] = []

    def job_fn(owner):
        async def fn(ctx):
            runs.append(owner)
            return {"ok": True}

        return fn

    clock = Clock()
    a = _scheduler(clock, jobs=(scheduler.Job("deneme", 60, job_fn("a")),), owner="a")
    b = _scheduler(clock, jobs=(scheduler.Job("deneme", 60, job_fn("b")),), owner="b")
    assert (await a.run_job("deneme")).status == "ok"
    assert (await b.run_job("deneme")).status == "skipped"
    assert (await a.run_job("deneme")).status == "skipped"  # aralık dolmadı
    clock.advance(seconds=59)
    assert (await b.run_job("deneme")).status == "skipped"
    clock.advance(seconds=1)
    assert (await b.run_job("deneme")).status == "ok"
    assert runs == ["a", "b"]
    lease = await session.get(SchedulerLease, "deneme")
    assert lease.owner == "b" and lease.runs == 2 and lease.last_status == "ok"


async def test_crashed_replica_lease_expires(session):
    # Kopya kirayı aldı ve çöktü (bırakmadı): kira süresi (aralık ya da zaman aşımı) dolana dek
    # başka kopya çalıştırmaz, sonra devralır
    async with db.get_sessionmaker()() as s:
        assert await scheduler.acquire_lease(s, "deneme", "olu", T0, timedelta(seconds=300))
    async with db.get_sessionmaker()() as s:
        assert not await scheduler.acquire_lease(s, "deneme", "canli", T0, timedelta(seconds=300))
        later = T0 + timedelta(seconds=300)
        assert await scheduler.acquire_lease(s, "deneme", "canli", later, timedelta(seconds=300))
    # Eski sahip artık kirayı bırakamaz (başkasının kirasına dokunmaz)
    async with db.get_sessionmaker()() as s:
        await scheduler.release_lease(s, "deneme", "olu", next_run=T0, finished=T0, status="ok")
    lease = await session.get(SchedulerLease, "deneme", populate_existing=True)
    assert lease.owner == "canli" and lease.lease_until.replace(tzinfo=UTC) > T0


async def test_job_error_is_recorded_and_does_not_stop_scheduler(session):
    async def boom(ctx):
        raise RuntimeError("https://gizli.ornek/hook?token=abc kırıldı")

    async def ok(ctx):
        return {"n": 1}

    s = _scheduler(Clock(), jobs=(scheduler.Job("bozuk", 30, boom), scheduler.Job("iyi", 30, ok)))
    runs = await s.run_due()
    assert [(r.job, r.status) for r in runs] == [("bozuk", "error"), ("iyi", "ok")]
    lease = await session.get(SchedulerLease, "bozuk")
    assert lease.last_status == "error" and "gizli.ornek" not in lease.last_error


async def test_start_and_stop_loop(session):
    calls: list[int] = []

    async def fn(ctx):
        calls.append(1)
        return {}

    s = scheduler.Scheduler(
        Settings(scheduler_tick_seconds=0.01),
        owner="dongu",
        jobs=(scheduler.Job("dongu", 3600, fn),),
        notifier_factory=lambda st: Recorder(),
    )
    s.start()
    assert s.running
    import asyncio

    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.01)
    await s.stop()
    assert not s.running and calls == [1]  # aralık dolmadan ikinci kez çalışmaz


# --- bildirim kuyruğu ------------------------------------------------------------------------------


async def test_outbox_dispatch_job_retries_with_backoff(session):
    notifications.stage(
        session,
        notifications.PendingNotification(
            event="oncall.page",
            payload={"message": "VPN kesintisi"},
            actor="ayse",
            entity_type="tool",
            entity_id="notify.oncall_page",
        ),
        now=T0,
    )
    await session.commit()
    clock, rec = Clock(), Recorder(fail=True)
    jobs = (scheduler.Job("outbox_dispatch", 1, scheduler.dispatch_outbox),)
    s = _scheduler(clock, rec, jobs=jobs)
    assert (await s.run_job("outbox_dispatch")).result == {"sent": 0, "failed": 1, "dead": 0}
    clock.advance(seconds=5)
    assert (await s.run_job("outbox_dispatch")).result["failed"] == 0  # geri çekilme sürüyor
    rec.fail = False
    clock.advance(seconds=5)
    assert (await s.run_job("outbox_dispatch")).result == {"sent": 1, "failed": 0, "dead": 0}
    row = (await session.scalars(select(NotificationOutbox))).one()
    await session.refresh(row)
    assert row.state == NotificationState.SENT and row.attempts == 2


# --- eylem isteği süre aşımı ---------------------------------------------------------------------------


async def test_action_expiry_job_expires_without_get_actions(session, directory):
    from serviszeka.tools import execute_tool

    agent = CurrentUser("ayse", frozenset({"agent"}))
    r = await execute_tool(session, agent, "identity.reset_mfa", {"user": "ali"}, settings=LIVE_REQ)
    await session.commit()
    created = (await session.get(ActionRequest, r.action_request_id)).expires_at
    clock = Clock(created.replace(tzinfo=UTC) - timedelta(minutes=1))
    s = _scheduler(clock, jobs=(scheduler.Job("action_expiry", 60, scheduler.expire_actions),))
    assert (await s.run_job("action_expiry")).result == {"expired": 0}
    clock.advance(minutes=2)
    assert (await s.run_job("action_expiry")).result == {"expired": 1}
    ar = await session.get(ActionRequest, r.action_request_id, populate_existing=True)
    assert ar.state == "expired"
    assert "action_request.expire" in await _audit_actions()
    assert writes(directory, "reset_mfa") == []


# --- SLA ihlal / risk uyarısı --------------------------------------------------------------------------


async def _incident(session, priority: Priority, created: datetime, minutes: int) -> Incident:
    inc = await incident_service.create_incident(
        session, IncidentCreate(title="ERP çöktü, ali@ornek.com.tr", caller="noc"), actor="noc"
    )
    inc.priority = priority
    inc.status = IncidentStatus.IN_PROGRESS
    inc.created_at = created
    inc.responded_at = created
    inc.response_due_at = None
    inc.resolve_due_at = created + timedelta(minutes=minutes)
    await session.commit()
    return inc


async def test_sla_check_pages_new_p1_p2_breaches_once_per_state(session):
    p1 = await _incident(session, Priority.P1, T0, 100)
    p3 = await _incident(session, Priority.P3, T0, 100)
    clock, rec = Clock(T0 + timedelta(minutes=10)), Recorder()
    jobs = (scheduler.Job("sla_check", 1, scheduler.check_sla),)
    s = _scheduler(clock, rec, jobs=jobs)
    assert (await s.run_job("sla_check")).result["staged"] == 0  # yolunda
    clock.advance(minutes=70)  # %80 -> risk
    run = await s.run_job("sla_check")
    assert run.result["staged"] == 1, run
    assert [e for e, _ in rec.calls] == ["sla.at_risk"]
    payload = rec.calls[0][1]
    assert payload["incident_number"] == p1.number and payload["sla_state"] == "at_risk"
    assert "ali@ornek.com.tr" not in str(payload)
    clock.advance(seconds=5)
    assert (await s.run_job("sla_check")).result["staged"] == 0  # aynı durum: tekrar uyarı yok
    clock.advance(minutes=30)  # ihlal
    assert (await s.run_job("sla_check")).result["staged"] == 1
    clock.advance(minutes=30)
    assert (await s.run_job("sla_check")).result["staged"] == 0
    assert [e for e, _ in rec.calls] == ["sla.at_risk", "sla.breached"]
    rows = (await session.scalars(select(NotificationOutbox))).all()
    keys = sorted(r.idempotency_key for r in rows)
    assert keys == [f"sla:{p1.id}:at_risk", f"sla:{p1.id}:breached"]
    assert all(r.entity_id != p3.id for r in rows)
    assert (await _audit_actions()).count("sla.alert") == 2


async def test_sla_check_two_replicas_page_once(session):
    await _incident(session, Priority.P2, T0, 60)
    clock, rec = Clock(T0 + timedelta(minutes=90)), Recorder()
    jobs = (scheduler.Job("sla_check", 60, scheduler.check_sla),)
    a, b = (
        _scheduler(clock, rec, jobs=jobs, owner="a"),
        _scheduler(clock, rec, jobs=jobs, owner="b"),
    )
    await a.run_due()
    await b.run_due()
    clock.advance(seconds=60)
    await b.run_due()  # kira b'ye geçti, ama tekilleştirme yine tek uyarı
    assert [e for e, _ in rec.calls] == ["sla.breached"]


async def test_sla_page_failure_is_retried_by_outbox(session):
    await _incident(session, Priority.P1, T0, 60)
    clock, rec = Clock(T0 + timedelta(minutes=90)), Recorder(fail=True)
    s = _scheduler(clock, rec)
    await s.run_job("sla_check")
    row = (await session.scalars(select(NotificationOutbox))).one()
    assert row.state == NotificationState.FAILED
    rec.fail = False
    clock.advance(seconds=30)
    await s.run_job("outbox_dispatch")
    await session.refresh(row)
    assert row.state == NotificationState.SENT and len(rec.calls) == 2


# --- hizmet talebi bekleme süre aşımı -----------------------------------------------------------------


async def test_request_business_approval_expires_and_cannot_be_retried(session, directory):
    item = await seeded_item(session, "shared_mailbox_access")
    req = await request_service.create_request(
        session,
        item,
        requester="ali",
        opened_by=ALI,
        form_values={"mailbox": "muhasebe"},
        settings=LIVE_REQ,
    )
    req.created_at = T0
    await session.commit()
    assert req.state == "awaiting_approval"
    clock = Clock(T0 + timedelta(hours=47))
    jobs = (scheduler.Job("request_expiry", 1, scheduler.expire_requests),)
    s = _scheduler(clock, jobs=jobs)
    assert (await s.run_job("request_expiry")).result == {"approval_expired": 0, "resumed": 0}
    clock.advance(hours=1)
    assert (await s.run_job("request_expiry")).result == {"approval_expired": 1, "resumed": 0}
    req = await session.get(ServiceRequest, req.id, populate_existing=True)
    assert req.state == "rejected" and req.approval_state == "rejected"
    assert req.touchless is False and req.approved_by is None
    assert "service_request.approval_expired" in await _audit_actions()
    # Süresi dolan iş onayı yeniden denemeyle atlanamaz
    with pytest.raises(request_service.RequestStateError):
        await request_service.retry(session, req.id, CurrentUser("ayse", frozenset({"agent"})))
    assert writes(directory, "grant_shared_access") == []


async def test_request_missed_hook_is_resumed(session, directory):
    item = await seeded_item(session, "mfa_reset")
    req = await request_service.create_request(
        session, item, requester="ali", opened_by=ALI, form_values={}, settings=LIVE_REQ
    )
    await session.commit()
    assert req.state == "fulfilling" and req.waiting_action_id
    # Eylem isteği başka yoldan süresi doldu ama talebe bildirilmedi (kaçan kanca)
    ar = await session.get(ActionRequest, req.waiting_action_id)
    ar.state = "expired"
    await session.commit()
    s = _scheduler(Clock(), jobs=(scheduler.Job("request_expiry", 1, scheduler.expire_requests),))
    assert (await s.run_job("request_expiry")).result == {"approval_expired": 0, "resumed": 1}
    req = await session.get(ServiceRequest, req.id, populate_existing=True)
    assert req.state == "failed" and req.waiting_action_id is None and req.touchless is False
    assert writes(directory, "reset_mfa") == []


# --- lifespan ---------------------------------------------------------------------------------------------


async def test_lifespan_starts_and_stops_scheduler(monkeypatch, tmp_path):
    from serviszeka import main

    db.init_engine(f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}")
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(
            environment="development",
            auth_mode="dev",
            scheduler_enabled=True,
            scheduler_tick_seconds=3600,
        ),
    )
    try:
        async with main.lifespan(main.app):
            sched = main.app.state.scheduler
            assert sched is not None and sched.running
        assert not sched.running
        monkeypatch.setattr(
            main,
            "get_settings",
            lambda: Settings(
                environment="development",
                auth_mode="dev",
                scheduler_enabled=False,
            ),
        )
        async with main.lifespan(main.app):
            assert main.app.state.scheduler is None
    finally:
        await db.dispose_engine()


def test_scheduler_disabled_in_tests_by_default():
    from serviszeka.config import get_settings

    assert get_settings().scheduler_enabled is False
    assert Settings.model_fields["scheduler_enabled"].default is True
