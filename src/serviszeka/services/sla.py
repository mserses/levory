"""SLA hedefleri, vade hesaplama ve ihlal / risk tespiti.

Saat hesabı `ServiceCalendar` arayüzü üzerinden yapılır; şimdilik 7x24 takvim
kullanılır, mesai saatli takvim aynı arayüzle eklenebilir.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.domain.enums import IncidentStatus, OnHoldReason, Priority
from serviszeka.models import Incident

AT_RISK_RATIO = 0.75

# Çözüm SLA'sının durduğu (artık izlenmeyen) durumlar
CLOSED_STATUSES: frozenset[IncidentStatus] = frozenset(
    {IncidentStatus.RESOLVED, IncidentStatus.CLOSED, IncidentStatus.CANCELLED}
)

# SLA saatini durduran bekleme nedenleri (değişiklik/iç bekleme saati durdurmaz)
PAUSING_HOLD_REASONS: frozenset[OnHoldReason] = frozenset(
    {OnHoldReason.AWAITING_CALLER, OnHoldReason.AWAITING_VENDOR}
)


def ensure_utc(ts: datetime) -> datetime:
    # SQLite saat dilimini düşürür: naive değerler UTC kabul edilir.
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


class ServiceCalendar(Protocol):
    """SLA saatini işleten takvim (7x24, mesai saatleri, tatiller...)."""

    def add(self, start: datetime, duration: timedelta) -> datetime: ...

    def elapsed(self, start: datetime, end: datetime) -> timedelta: ...


class Calendar24x7:
    """Kesintisiz saat: her dakika SLA süresinden sayılır."""

    def add(self, start: datetime, duration: timedelta) -> datetime:
        return ensure_utc(start) + duration

    def elapsed(self, start: datetime, end: datetime) -> timedelta:
        return max(ensure_utc(end) - ensure_utc(start), timedelta(0))


@dataclass(frozen=True)
class SLATarget:
    response: timedelta
    resolve: timedelta


DEFAULT_TARGETS: Mapping[Priority, SLATarget] = {
    Priority.P1: SLATarget(timedelta(minutes=15), timedelta(hours=4)),
    Priority.P2: SLATarget(timedelta(minutes=30), timedelta(hours=8)),
    Priority.P3: SLATarget(timedelta(hours=4), timedelta(hours=24)),
    Priority.P4: SLATarget(timedelta(hours=8), timedelta(hours=72)),
    Priority.P5: SLATarget(timedelta(hours=24), timedelta(hours=120)),
}


class SLAMetric(StrEnum):
    RESPONSE = "response"
    RESOLVE = "resolve"


class SLAState(StrEnum):
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"  # Pencerenin >= %75'i geçti
    BREACHED = "breached"  # Vade geçti, hedef karşılanmadı
    MET = "met"  # Hedef vadesinde karşılandı
    MISSED = "missed"  # Hedef karşılandı ama vadeden sonra


@dataclass(frozen=True)
class SLADueDates:
    response_due_at: datetime
    resolve_due_at: datetime


@dataclass(frozen=True)
class SLAPolicy:
    targets: Mapping[Priority, SLATarget] = field(default_factory=lambda: dict(DEFAULT_TARGETS))
    calendar: ServiceCalendar = field(default_factory=Calendar24x7)
    at_risk_ratio: float = AT_RISK_RATIO

    def target(self, priority: Priority) -> SLATarget:
        return self.targets[Priority(priority)]

    def due_dates(self, priority: Priority, start: datetime) -> SLADueDates:
        t = self.target(priority)
        return SLADueDates(
            response_due_at=self.calendar.add(start, t.response),
            resolve_due_at=self.calendar.add(start, t.resolve),
        )

    def elapsed_ratio(
        self,
        start: datetime,
        due: datetime,
        now: datetime,
        paused: timedelta = timedelta(0),
    ) -> float:
        # Vade, duraklatılan süre kadar ötelenmiştir; oran duraklama hariç süreyle hesaplanır
        window = self.calendar.elapsed(start, due) - paused
        if window <= timedelta(0):
            return 1.0
        elapsed = max(self.calendar.elapsed(start, now) - paused, timedelta(0))
        return elapsed / window

    def state(
        self,
        *,
        start: datetime,
        due: datetime,
        now: datetime,
        completed_at: datetime | None = None,
        paused: timedelta = timedelta(0),
    ) -> SLAState:
        due = ensure_utc(due)
        if completed_at is not None:
            return SLAState.MET if ensure_utc(completed_at) <= due else SLAState.MISSED
        if ensure_utc(now) > due:
            return SLAState.BREACHED
        if self.elapsed_ratio(start, due, now, paused) >= self.at_risk_ratio:
            return SLAState.AT_RISK
        return SLAState.ON_TRACK


DEFAULT_POLICY = SLAPolicy()


def compute_due_dates(
    priority: Priority, start: datetime, policy: SLAPolicy | None = None
) -> SLADueDates:
    return (policy or DEFAULT_POLICY).due_dates(priority, start)


def is_pausing_reason(reason: OnHoldReason | str | None) -> bool:
    return reason is not None and OnHoldReason(reason) in PAUSING_HOLD_REASONS


def paused_duration(paused_at: datetime, now: datetime) -> timedelta:
    """Duraklatma başlangıcından bu yana geçen süre (tam saniye, negatif olamaz)."""
    seconds = int((ensure_utc(now) - ensure_utc(paused_at)).total_seconds())
    return timedelta(seconds=max(seconds, 0))


def shift_due(due: datetime, by: timedelta) -> datetime:
    """Vadeyi duraklatılan süre kadar ileri öteler."""
    return ensure_utc(due) + by


def is_paused(incident: Incident) -> bool:
    return incident.sla_paused_at is not None and incident.status not in CLOSED_STATUSES


@dataclass(frozen=True)
class SLAAlert:
    incident_id: str
    number: str
    priority: Priority
    metric: SLAMetric
    state: SLAState  # AT_RISK veya BREACHED
    due_at: datetime
    elapsed_ratio: float


def evaluate_incident(
    incident: Incident, now: datetime, policy: SLAPolicy | None = None
) -> list[SLAAlert]:
    """Bir olay için risk/ihlal uyarılarını döndürür (yolunda olanlar dahil edilmez)."""
    policy = policy or DEFAULT_POLICY
    now = ensure_utc(now)
    if incident.status in CLOSED_STATUSES or incident.created_at is None:
        return []
    # Saat durmuşken (arayan/tedarikçi beklemesi) saat duraklama anında donar: risk
    # raporlanmaz, ama duraklamadan önce gerçekleşmiş ihlal gizlenemez
    paused_now = is_paused(incident)
    if paused_now:
        now = ensure_utc(incident.sla_paused_at)
    start = ensure_utc(incident.created_at)
    paused = timedelta(seconds=incident.sla_paused_seconds or 0)
    checks: list[tuple[SLAMetric, datetime | None, datetime | None]] = [
        (SLAMetric.RESPONSE, incident.response_due_at, incident.responded_at),
        (SLAMetric.RESOLVE, incident.resolve_due_at, incident.resolved_at),
    ]
    alerts: list[SLAAlert] = []
    for metric, due, done in checks:
        if due is None or done is not None:
            continue
        state = policy.state(start=start, due=due, now=now, paused=paused)
        if paused_now and state != SLAState.BREACHED:
            continue
        if state in (SLAState.AT_RISK, SLAState.BREACHED):
            alerts.append(
                SLAAlert(
                    incident_id=incident.id,
                    number=incident.number,
                    priority=incident.priority,
                    metric=metric,
                    state=state,
                    due_at=ensure_utc(due),
                    elapsed_ratio=round(policy.elapsed_ratio(start, due, now, paused), 4),
                )
            )
    return alerts


async def check_breaches(
    session: AsyncSession, now: datetime | None = None, policy: SLAPolicy | None = None
) -> list[SLAAlert]:
    """Açık olaylardaki SLA ihlallerini ve riskleri döndürür (önce ihlaller, sonra vade)."""
    now = ensure_utc(now or datetime.now(UTC))
    stmt = select(Incident).where(
        Incident.status.not_in(list(CLOSED_STATUSES)),
        or_(Incident.response_due_at.is_not(None), Incident.resolve_due_at.is_not(None)),
    )
    alerts: list[SLAAlert] = []
    for incident in (await session.scalars(stmt)).all():
        alerts.extend(evaluate_incident(incident, now, policy))
    alerts.sort(key=lambda a: (a.state != SLAState.BREACHED, a.due_at))
    return alerts
