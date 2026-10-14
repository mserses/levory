"""Olay yönetimi servis testleri."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from serviszeka import audit
from serviszeka.domain.enums import Impact, IncidentStatus, Priority, TicketKind, Urgency
from serviszeka.models import AuditLog
from serviszeka.services import incidents as svc
from serviszeka.services import sla
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate


def _new(**kw):
    data = {"title": "VPN çalışmıyor", "description": "Bağlanamıyorum", "caller": "ayse"}
    data.update(kw)
    return IncidentCreate(**data)


async def test_create_numbering_priority_sla_and_audit(session):
    a = await svc.create_incident(session, _new(impact="high", urgency="medium"), "ayse")
    b = await svc.create_incident(session, _new(), "ayse")
    assert (a.number, b.number) == ("INC0000001", "INC0000002")
    assert a.priority == Priority.P2
    assert b.priority == Priority.P5  # varsayılan düşük/düşük
    assert a.kind == TicketKind.INCIDENT and a.status == IncidentStatus.NEW
    assert a.version == 1
    assert a.response_due_at == a.created_at + timedelta(minutes=30)
    assert a.resolve_due_at == a.created_at + timedelta(hours=8)
    logs = (await session.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
    assert [log.action for log in logs] == ["incident.create", "incident.create"]
    assert logs[0].entity_id == a.id and logs[0].details["number"] == "INC0000001"
    assert await audit.verify_chain(session)


async def test_update_increments_version_and_recomputes_priority(session):
    inc = await svc.create_incident(session, _new(), "ayse")
    created = inc.created_at
    inc = await svc.update_incident(
        session, inc.id, IncidentUpdate(impact=Impact.HIGH, urgency=Urgency.HIGH), "mehmet"
    )
    assert inc.version == 2
    assert inc.priority == Priority.P1
    assert sla.ensure_utc(inc.response_due_at) == sla.ensure_utc(created) + timedelta(minutes=15)
    log = await session.scalar(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1))
    assert log.action == "incident.update" and log.actor == "mehmet"
    assert log.details["changes"]["priority"] == ["P5", "P1"]
    assert log.details["version"] == 2


async def test_noop_update_does_not_bump_version(session):
    inc = await svc.create_incident(session, _new(), "ayse")
    inc = await svc.update_incident(session, inc.id, IncidentUpdate(title=inc.title), "x")
    assert inc.version == 1
    count = len((await session.scalars(select(AuditLog))).all())
    assert count == 1


async def test_optimistic_locking(session):
    inc = await svc.create_incident(session, _new(), "ayse")
    await svc.update_incident(
        session, inc.id, IncidentUpdate(assignee="ali"), "x", expected_version=1
    )
    with pytest.raises(svc.VersionConflict) as ei:
        await svc.update_incident(
            session, inc.id, IncidentUpdate(assignee="veli"), "y", expected_version=1
        )
    assert ei.value.expected == 1 and ei.value.actual == 2
    assert (await svc.get_incident(session, inc.id)).assignee == "ali"


async def test_status_lifecycle(session):
    inc = await svc.create_incident(session, _new(), "ayse")
    assert inc.responded_at is None
    inc = await svc.update_incident(session, inc.id, IncidentUpdate(status="in_progress"), "x")
    first_response = inc.responded_at
    assert first_response is not None

    # Çözüm metni olmadan çözülemez
    with pytest.raises(svc.InvalidTransitionError):
        await svc.update_incident(session, inc.id, IncidentUpdate(status="resolved"), "x")

    inc = await svc.update_incident(
        session, inc.id, IncidentUpdate(status="resolved", resolution="Sertifika yenilendi"), "x"
    )
    assert inc.resolved_at is not None
    # Yeniden açma çözüm zamanını sıfırlar, ilk yanıt zamanı korunur
    inc = await svc.update_incident(session, inc.id, IncidentUpdate(status="in_progress"), "x")
    assert inc.resolved_at is None and inc.responded_at == first_response
    inc = await svc.update_incident(session, inc.id, IncidentUpdate(status="resolved"), "x")
    inc = await svc.update_incident(session, inc.id, IncidentUpdate(status="closed"), "x")
    assert inc.status == IncidentStatus.CLOSED

    with pytest.raises(svc.InvalidTransitionError):
        await svc.update_incident(session, inc.id, IncidentUpdate(status="in_progress"), "x")
    with pytest.raises(svc.InvalidTransitionError):
        await svc.update_incident(session, inc.id, IncidentUpdate(title="yeni"), "x")


async def test_cannot_close_from_new(session):
    inc = await svc.create_incident(session, _new(), "ayse")
    with pytest.raises(svc.InvalidTransitionError):
        await svc.update_incident(session, inc.id, IncidentUpdate(status="closed"), "x")


async def test_not_found(session):
    with pytest.raises(svc.IncidentNotFoundError):
        await svc.update_incident(session, "yok", IncidentUpdate(title="a"), "x")
    assert await svc.get_incident(session, "yok") is None


async def test_list_filters(session):
    a = await svc.create_incident(session, _new(impact="high", urgency="high"), "u")
    b = await svc.create_incident(session, _new(), "u")
    await svc.update_incident(session, b.id, IncidentUpdate(assignee="ali", status="assigned"), "u")
    assert {i.id for i in await svc.list_incidents(session)} == {a.id, b.id}
    assert [i.id for i in await svc.list_incidents(session, priority="P1")] == [a.id]
    assert [i.id for i in await svc.list_incidents(session, assignee="ali")] == [b.id]
    assert [i.id for i in await svc.list_incidents(session, status=IncidentStatus.NEW)] == [a.id]
    assert len(await svc.list_incidents(session, limit=1)) == 1


async def test_audit_chain_valid_after_many_changes(session):
    inc = await svc.create_incident(session, _new(), "u")
    for g in ("ag1", "ag2", "ag3"):
        await svc.update_incident(session, inc.id, IncidentUpdate(assignment_group=g), "u")
    await session.commit()
    assert inc.version == 4
    assert await audit.verify_chain(session)
