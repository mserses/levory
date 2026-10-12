"""SLA hedefleri, vade hesaplama ve ihlal/risk tespiti testleri."""

from datetime import UTC, datetime, timedelta

import pytest

from serviszeka.domain.enums import Impact, IncidentStatus, Priority, Urgency
from serviszeka.services import incidents as svc
from serviszeka.services import sla
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("priority", "response", "resolve"),
    [
        (Priority.P1, timedelta(minutes=15), timedelta(hours=4)),
        (Priority.P2, timedelta(minutes=30), timedelta(hours=8)),
        (Priority.P3, timedelta(hours=4), timedelta(hours=24)),
        (Priority.P4, timedelta(hours=8), timedelta(hours=72)),
        (Priority.P5, timedelta(hours=24), timedelta(hours=120)),
    ],
)
def test_due_dates_per_priority(priority, response, resolve):
    due = sla.compute_due_dates(priority, T0)
    assert due.response_due_at == T0 + response
    assert due.resolve_due_at == T0 + resolve


def test_naive_start_treated_as_utc():
    due = sla.compute_due_dates(Priority.P1, T0.replace(tzinfo=None))
    assert due.response_due_at == T0 + timedelta(minutes=15)


def test_state_transitions():
    p = sla.DEFAULT_POLICY
    due = T0 + timedelta(hours=4)
    assert p.state(start=T0, due=due, now=T0 + timedelta(hours=1)) == sla.SLAState.ON_TRACK
    # Tam %75 sınırı risklidir
    assert p.state(start=T0, due=due, now=T0 + timedelta(hours=3)) == sla.SLAState.AT_RISK
    assert p.state(start=T0, due=due, now=due) == sla.SLAState.AT_RISK
    assert p.state(start=T0, due=due, now=due + timedelta(seconds=1)) == sla.SLAState.BREACHED
    done_early = T0 + timedelta(hours=2)
    assert p.state(start=T0, due=due, now=due, completed_at=done_early) == sla.SLAState.MET
    late = due + timedelta(minutes=1)
    assert p.state(start=T0, due=due, now=late, completed_at=late) == sla.SLAState.MISSED


class _Doubling:
    """Özel takvim örneği: arayüzün takılabilir olduğunu doğrular."""

    def add(self, start, duration):
        return sla.ensure_utc(start) + duration * 2

    def elapsed(self, start, end):
        return (sla.ensure_utc(end) - sla.ensure_utc(start)) / 2


def test_custom_calendar_and_targets():
    policy = sla.SLAPolicy(calendar=_Doubling())
    due = policy.due_dates(Priority.P1, T0)
    assert due.response_due_at == T0 + timedelta(minutes=30)


async def _make(session, impact, urgency, created_at):
    inc = await svc.create_incident(
        session,
        IncidentCreate(title="t", caller="c", impact=impact, urgency=urgency),
        actor="u",
    )
    inc.created_at = created_at
    due = sla.compute_due_dates(inc.priority, created_at)
    inc.response_due_at, inc.resolve_due_at = due.response_due_at, due.resolve_due_at
    await session.flush()
    return inc


async def test_check_breaches(session):
    p1 = await _make(session, Impact.HIGH, Urgency.HIGH, T0)  # P1: 15dk / 4s
    p5 = await _make(session, Impact.LOW, Urgency.LOW, T0)  # P5: 24s / 120s
    p3 = await _make(session, Impact.MEDIUM, Urgency.MEDIUM, T0)  # P3: 4s / 24s
    await svc.update_incident(
        session, p3.id, IncidentUpdate(status=IncidentStatus.IN_PROGRESS), "u"
    )
    # Çözülen olay izlenmez
    done = await _make(session, Impact.HIGH, Urgency.HIGH, T0)
    await svc.update_incident(
        session, done.id, IncidentUpdate(status="resolved", resolution="ok"), "u"
    )
    await session.commit()

    now = T0 + timedelta(hours=3, minutes=30)
    alerts = await sla.check_breaches(session, now)
    got = {(a.number, a.metric.value, a.state.value) for a in alerts}
    assert (p1.number, "response", "breached") in got
    assert (p1.number, "resolve", "at_risk") in got  # 3.5/4 = %87.5
    assert not any(a.number == p5.number for a in alerts)
    # p3 yanıtlandı (responded_at gerçek saat), çözüm penceresi %14 -> uyarı yok
    assert not any(a.number == p3.number for a in alerts)
    assert not any(a.number == done.number for a in alerts)
    # İhlaller önce gelir
    assert alerts[0].state == sla.SLAState.BREACHED

    later = await sla.check_breaches(session, T0 + timedelta(hours=5))
    assert {(a.metric.value, a.state.value) for a in later if a.number == p1.number} == {
        ("response", "breached"),
        ("resolve", "breached"),
    }
