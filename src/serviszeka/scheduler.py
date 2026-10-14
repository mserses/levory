"""Arka plan zamanlayıcısı (Faz 4): FastAPI lifespan içinde çalışan periyodik işler.

İşler (her biri kendi oturumunda, kendi işlemleriyle; hata zamanlayıcıyı durdurmaz):

- `outbox_dispatch` (30 sn): kalıcı bildirim kuyruğundaki zamanı gelmiş satırları gönderir
  (`notifications.dispatch_due`; üstel geri çekilme, en az bir kez).
- `action_expiry` (60 sn): süresi dolan bekleyen eylem isteklerini `expired` yapar
  (`tools.actions.expire_due`; önceden yalnızca `GET /actions` çağrısında oluyordu). Bağlı hizmet
  talebi `failed` olur.
- `sla_check` (60 sn): açık P1/P2 olaylarda SLA ihlali ya da riski için nöbetçi bildirimi kuyruğa
  ekler; olay + durum (`at_risk` / `breached`) başına bir kez (`notification_outbox.idempotency_key`
  = `sla:<olay>:<durum>`), denetime `sla.alert` yazar.
- `request_expiry` (300 sn): iş onayı süresi dolan talepleri reddeder, kancası kaçmış bekleyen
  talepleri ilerletir (`services.requests.expire_waiting`).
- `csi_review` (günde bir): sürekli iyileştirme ajanı (`agents/improvement.run_review`, `ai:csi`)
  KPI/SLA tahmini/bilinen hata/PIR sinyallerinden fikir önerir, ölçüm penceresi dolanı ölçer.

Birden fazla kopya (replica): her iş için `scheduler_leases` satırı kiralanır. Kira, karşılaştırmalı
güncellemeyle (compare-and-set: `UPDATE ... WHERE job = :j AND lease_until <= :now`) alınır; aynı
anda yalnızca bir kopya başarılı olur (PostgreSQL ve SQLite'ta aynı semantik; satır yoksa `INSERT`,
tekil anahtar çakışması "alınamadı" sayılır). Kira süresi `max(aralık, iş zaman aşımı + 60 sn
pay)` (iptal gecikmesi / küçük saat kayması çift çalıştırmasın; kopyaların saati NTP ile eşit
olmalı); iş bitince
`lease_until = başlangıç + aralık` yapılır — böylece aralık da kopyalar arasında korunur. Çöken
kopyanın kirası süresi dolunca başka kopyaya geçer.

`SZ_SCHEDULER_ENABLED` (varsayılan true; testlerde false) ve `SZ_SCHEDULER_TICK_SECONDS`. Testler
işleri sahte saatle (`Scheduler(clock=...)`, `run_due`, `run_job`) doğrudan sürer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from serviszeka import audit, notifications
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import Priority
from serviszeka.models import Incident, SchedulerLease
from serviszeka.privacy import mask_pii

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "system:scheduler"
SLA_PRIORITIES = frozenset({Priority.P1, Priority.P2})
SLA_EVENTS = {"breached": "sla.breached", "at_risk": "sla.at_risk"}
DEFAULT_JOB_TIMEOUT = 300.0
# Kira, iş zaman aşımından bu kadar uzun tutulur: zaman aşımında iptal edilen iş (veritabanı
# çağrısı iptali gecikebilir) ya da kopyalar arası küçük saat kayması çift çalıştırma yaratmaz
LEASE_MARGIN_SECONDS = 60.0


@dataclass
class JobContext:
    session: AsyncSession
    now: datetime
    settings: Settings
    notifier: notifications.Notifier


JobFn = Callable[[JobContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Job:
    name: str
    interval: float  # saniye
    fn: JobFn
    timeout: float = DEFAULT_JOB_TIMEOUT


@dataclass
class JobRun:
    job: str
    status: str  # ok | error | skipped
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# --- işler ----------------------------------------------------------------------------------------


async def dispatch_outbox(ctx: JobContext) -> dict[str, Any]:
    actions = await notifications.dispatch_due(
        ctx.session, ctx.notifier, now=ctx.now, settings=ctx.settings
    )
    return {
        "sent": actions.count("notification.sent"),
        "failed": actions.count("notification.failed"),
        "dead": actions.count("notification.dead"),
    }


async def expire_actions(ctx: JobContext) -> dict[str, Any]:
    from serviszeka.tools import actions

    expired = await actions.expire_due(ctx.session, ctx.now)
    await ctx.session.commit()
    await notifications.send_pending(ctx.session, ctx.notifier, now=ctx.now, settings=ctx.settings)
    return {"expired": expired}


def sla_dedup_key(incident_id: str, state: str) -> str:
    return f"sla:{incident_id}:{state}"


async def check_sla(ctx: JobContext) -> dict[str, Any]:
    """Yeni P1/P2 SLA ihlali / riski için nöbetçi bildirimi (olay + durum başına bir kez)."""
    from serviszeka.services import sla

    session = ctx.session
    alerts = await sla.check_breaches(session, ctx.now)
    seen: set[str] = set()
    staged = 0
    for alert in alerts:  # önce ihlaller
        if Priority(alert.priority) not in SLA_PRIORITIES:
            continue
        state = sla.SLAState(alert.state).value
        key = sla_dedup_key(alert.incident_id, state)
        if key in seen or await notifications.exists(session, key):
            seen.add(key)
            continue
        seen.add(key)
        incident = await session.get(Incident, alert.incident_id)
        if incident is None:  # pragma: no cover - eşzamanlı silme
            continue
        metric = sla.SLAMetric(alert.metric).value
        label = "SLA ihlal edildi" if state == "breached" else "SLA risk altında"
        payload = {
            "incident_id": incident.id,
            "incident_number": incident.number,
            "title": mask_pii(incident.title or ""),
            "message": f"{incident.number}: {label} ({metric}, {Priority(alert.priority).value})",
            "severity": "critical" if state == "breached" else "high",
            "priority": Priority(alert.priority).value,
            "sla_state": state,
            "metric": metric,
            "due_at": alert.due_at.isoformat(),
            "elapsed_ratio": alert.elapsed_ratio,
        }
        notifications.stage(
            session,
            notifications.PendingNotification(
                event=SLA_EVENTS[state],
                payload=payload,
                actor=SYSTEM_ACTOR,
                entity_type="incident",
                entity_id=incident.id,
                details={"sla_state": state, "metric": metric},
            ),
            idempotency_key=key,
            now=ctx.now,
        )
        await audit.record(
            session,
            actor=SYSTEM_ACTOR,
            action="sla.alert",
            entity_type="incident",
            entity_id=incident.id,
            details={
                "number": incident.number,
                "sla_state": state,
                "metric": metric,
                "priority": Priority(alert.priority).value,
                "due_at": alert.due_at.isoformat(),
            },
        )
        staged += 1
    await session.commit()
    if staged:
        await notifications.send_pending(session, ctx.notifier, now=ctx.now, settings=ctx.settings)
    return {"alerts": len(alerts), "staged": staged}


async def expire_requests(ctx: JobContext) -> dict[str, Any]:
    from serviszeka.services import requests as request_service

    result = await request_service.expire_waiting(ctx.session, now=ctx.now, settings=ctx.settings)
    await ctx.session.commit()
    await notifications.send_pending(ctx.session, ctx.notifier, now=ctx.now, settings=ctx.settings)
    return result


async def review_improvements(ctx: JobContext) -> dict[str, Any]:
    """Sürekli iyileştirme ajanı (`ai:csi`): sinyallerden fikir önerir, ölçümü geleni ölçer."""
    from serviszeka import llm as llm_module
    from serviszeka.agents import improvement

    try:
        llm = llm_module.get_llm(ctx.settings)
    except llm_module.LLMError:
        llm = None  # LLM yoksa deterministik gerekçe yazılır
    result = await improvement.run_review(ctx.session, llm=llm, settings=ctx.settings, now=ctx.now)
    await ctx.session.commit()
    await notifications.send_pending(ctx.session, ctx.notifier, now=ctx.now, settings=ctx.settings)
    return result


DEFAULT_JOBS: tuple[Job, ...] = (
    Job("outbox_dispatch", 30, dispatch_outbox),
    Job("action_expiry", 60, expire_actions),
    Job("sla_check", 60, check_sla),
    Job("request_expiry", 300, expire_requests),
    Job("csi_review", 86_400, review_improvements, timeout=600),
)


# --- kira (lease) ---------------------------------------------------------------------------------


async def acquire_lease(
    session: AsyncSession, job: str, owner: str, now: datetime, ttl: timedelta
) -> bool:
    """Süresi dolmuş kirayı karşılaştırmalı güncellemeyle alır; satır yoksa ekler. Commit eder."""
    result = await session.execute(
        update(SchedulerLease)
        .where(SchedulerLease.job == job, SchedulerLease.lease_until <= now)
        .values(
            owner=owner, lease_until=now + ttl, last_started_at=now, runs=SchedulerLease.runs + 1
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        await session.commit()
        return True
    await session.rollback()
    if await session.get(SchedulerLease, job) is not None:
        await session.rollback()
        return False  # başka kopyanın geçerli kirası var
    session.add(
        SchedulerLease(job=job, owner=owner, lease_until=now + ttl, last_started_at=now, runs=1)
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()  # aynı anda başka kopya ekledi
        return False
    return True


async def release_lease(
    session: AsyncSession,
    job: str,
    owner: str,
    *,
    next_run: datetime,
    finished: datetime,
    status: str,
    error: str | None = None,
) -> None:
    """Kirayı bir sonraki çalışma zamanına çeker (yalnızca sahibi; başkası aldıysa dokunmaz)."""
    await session.execute(
        update(SchedulerLease)
        .where(SchedulerLease.job == job, SchedulerLease.owner == owner)
        .values(
            lease_until=next_run, last_finished_at=finished, last_status=status, last_error=error
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()


# --- zamanlayıcı ------------------------------------------------------------------------------------


def _default_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


class Scheduler:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        clock: Callable[[], datetime] | None = None,
        notifier_factory: Callable[[Settings], notifications.Notifier] | None = None,
        jobs: tuple[Job, ...] = DEFAULT_JOBS,
        owner: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._sessionmaker = sessionmaker
        self.clock = clock or (lambda: datetime.now(UTC))
        self.notifier_factory = notifier_factory or notifications.get_notifier
        self.jobs = {job.name: job for job in jobs}
        self.owner = owner or _default_owner()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            from serviszeka.db import get_sessionmaker

            return get_sessionmaker()
        return self._sessionmaker

    async def run_job(self, name: str) -> JobRun:
        """Kirayı alabilirse işi bir kez çalıştırır (hata yükseltilmez)."""
        job = self.jobs[name]
        now = self.clock()
        ttl = timedelta(seconds=max(job.interval, job.timeout + LEASE_MARGIN_SECONDS))
        async with self.sessionmaker() as session:
            try:
                if not await acquire_lease(session, name, self.owner, now, ttl):
                    return JobRun(name, "skipped")
            except Exception as exc:
                logger.exception("Zamanlayıcı kirası alınamadı: %s", name)
                await session.rollback()
                return JobRun(name, "error", error=type(exc).__name__)
        status, result, error = "ok", {}, None
        async with self.sessionmaker() as session:
            ctx = JobContext(session, now, self.settings, self.notifier_factory(self.settings))
            try:
                result = await asyncio.wait_for(job.fn(ctx), timeout=job.timeout)
            except Exception as exc:  # iş hatası zamanlayıcıyı durdurmaz
                logger.exception("Zamanlayıcı işi başarısız: %s", name)
                await session.rollback()
                status, error = "error", notifications.safe_error(exc)
        async with self.sessionmaker() as session:
            try:
                await release_lease(
                    session,
                    name,
                    self.owner,
                    next_run=now + timedelta(seconds=job.interval),
                    finished=self.clock(),
                    status=status,
                    error=error,
                )
            except Exception:
                logger.exception("Zamanlayıcı kirası bırakılamadı: %s", name)
                await session.rollback()
        return JobRun(name, status, result, error)

    async def run_due(self) -> list[JobRun]:
        """Tüm işleri dener; kirası (aralığı) dolmamış ya da başka kopyadaki işler atlanır."""
        return [await self.run_job(name) for name in self.jobs]

    async def _loop(self) -> None:
        tick = float(self.settings.scheduler_tick_seconds)
        while not self._stop.is_set():
            try:
                await self.run_due()
            except Exception:  # pragma: no cover - run_job hataları zaten yutar
                logger.exception("Zamanlayıcı turu başarısız")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=tick)
            except TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="serviszeka-scheduler")
            logger.info("Zamanlayıcı başladı (%s).", self.owner)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=30)
        except TimeoutError:  # pragma: no cover
            self._task.cancel()
        self._task = None
        logger.info("Zamanlayıcı durdu (%s).", self.owner)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()
