"""Senkron motoru ile SLA duraklatmanın birlikte çalışması (entegrasyon) testleri."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from test_sync import FakeConnector

from serviszeka.domain.enums import (
    ExternalSystem,
    Impact,
    IncidentStatus,
    OnHoldReason,
    Priority,
    Urgency,
)
from serviszeka.integrations.sync import SyncEngine
from serviszeka.models import AuditLog
from serviszeka.services import incidents as svc
from serviszeka.services import sla
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate


async def _paused_incident(session, monkeypatch, fake):
    """Bir saat önce arayan beklemesine alınmış (saati durmuş), senkronlanmış P2 olay."""
    inc = await svc.create_incident(
        session,
        IncidentCreate(title="VPN", caller="ayse", impact=Impact.HIGH, urgency=Urgency.MEDIUM),
        "ayse",
    )
    engine = SyncEngine(session, [fake])
    await engine.push(inc)
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setattr(svc, "_now", lambda: hour_ago)
    await svc.update_incident(
        session, inc.id,
        IncidentUpdate(status=IncidentStatus.ON_HOLD, on_hold_reason=OnHoldReason.AWAITING_CALLER),
        "ali",
    )
    monkeypatch.undo()
    assert inc.sla_paused_at is not None
    await engine.push(inc)
    return inc, engine


async def _actions(session):
    return [a for a in (await session.scalars(select(AuditLog.action))).all()]


async def test_sync_leaving_hold_resumes_sla(session, monkeypatch):
    fake = FakeConnector()
    inc, engine = await _paused_incident(session, monkeypatch, fake)
    resolve_due = sla.ensure_utc(inc.resolve_due_at)

    fake.remote_edit("r1", status=IncidentStatus.IN_PROGRESS)
    res = await engine.pull(ExternalSystem.SERVICENOW)
    assert res["updated"] == 1
    assert inc.status == IncidentStatus.IN_PROGRESS
    assert inc.sla_paused_at is None and inc.on_hold_reason is None
    assert inc.sla_paused_seconds >= 3600
    assert sla.ensure_utc(inc.resolve_due_at) == resolve_due + timedelta(
        seconds=inc.sla_paused_seconds
    )
    assert "incident.sla_resume" in await _actions(session)


async def test_sync_resolving_from_hold_shifts_resolve_due(session, monkeypatch):
    fake = FakeConnector()
    inc, engine = await _paused_incident(session, monkeypatch, fake)
    resolve_due = sla.ensure_utc(inc.resolve_due_at)

    fake.remote_edit("r1", status=IncidentStatus.RESOLVED)
    await engine.pull(ExternalSystem.SERVICENOW)
    assert inc.resolved_at is not None and inc.sla_paused_at is None
    assert sla.ensure_utc(inc.resolve_due_at) == resolve_due + timedelta(
        seconds=inc.sla_paused_seconds
    )


async def test_sync_priority_change_keeps_paused_time(session):
    fake = FakeConnector()
    inc = await svc.create_incident(
        session, IncidentCreate(title="Yazıcı", caller="ayse"), "ayse"
    )
    inc.sla_paused_seconds = 600
    engine = SyncEngine(session, [fake])
    await engine.push(inc)

    fake.remote_edit("r1", priority=Priority.P2)
    await engine.pull(ExternalSystem.SERVICENOW)
    assert inc.priority == Priority.P2
    due = sla.compute_due_dates(Priority.P2, inc.created_at)
    assert sla.ensure_utc(inc.response_due_at) == due.response_due_at + timedelta(seconds=600)
    assert sla.ensure_utc(inc.resolve_due_at) == due.resolve_due_at + timedelta(seconds=600)
