"""İnceleme sırasında bulunan hataların regresyon testleri."""

import math
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from serviszeka import audit, db, knowledge, policy
from serviszeka.agents import service_desk
from serviszeka.api import integrations as integrations_api
from serviszeka.api.audit import AuditEntryOut
from serviszeka.api.integrations import SyncEventOut
from serviszeka.config import Settings
from serviszeka.domain.enums import (
    DecisionOutcome,
    ExternalSystem,
    Impact,
    IncidentStatus,
    Priority,
    Urgency,
)
from serviszeka.integrations.base import ConnectorError, RemoteTicket
from serviszeka.integrations.jira_sm import JiraSMConnector
from serviszeka.integrations.sync import SyncEngine
from serviszeka.knowledge.schemas import ArticleUpdate
from serviszeka.llm import FakeLLM
from serviszeka.models import AuditLog, ExternalLink, Incident, SyncEvent
from serviszeka.privacy import mask_pii
from serviszeka.services import incidents as svc
from serviszeka.services import sla
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate

LIVE = Settings(ai_shadow_mode=False, anthropic_api_key=None)


def _llm(**kw):
    data = {
        "kind": "incident",
        "category": "network",
        "service": "VPN",
        "impact": "low",
        "urgency": "low",
        "assignment_group": "Ağ Ekibi",
        "suggested_resolution": None,
        "kb_article_ids": [],
        "confidence": 0.95,
        "rationale": "test",
    }
    data.update(kw)
    return data


# --- politika ----------------------------------------------------------------


@pytest.mark.parametrize("conf", [math.nan, math.inf, 1.5, -0.1])
def test_policy_never_auto_applies_invalid_confidence(conf):
    d = policy.evaluate(agent="service_desk", action="classify", confidence=conf, settings=LIVE)
    assert d.outcome == DecisionOutcome.ESCALATED


# --- hizmet masası ajanı -----------------------------------------------------


async def test_triage_cannot_auto_downgrade_major_incident(session):
    inc = await svc.create_incident(
        session,
        IncidentCreate(title="Tüm şube ağı çöktü", caller="noc", impact="high", urgency="high"),
        actor="noc",
    )
    assert inc.priority == Priority.P1
    # Açıklamadaki talimatla LLM olayı P5'e düşürmeye ikna edilmiş olsun
    decision = await service_desk.triage(session, inc, FakeLLM([_llm()]), settings=LIVE)
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert inc.priority == Priority.P1
    assert inc.impact == Impact.HIGH


async def test_triage_on_closed_incident_does_not_crash(session):
    inc = await svc.create_incident(
        session, IncidentCreate(title="VPN yok", caller="ayse"), actor="ayse"
    )
    await svc.update_incident(
        session, inc.id, IncidentUpdate(status="resolved", resolution="ok"), "ayse"
    )
    await svc.update_incident(session, inc.id, IncidentUpdate(status="closed"), "ayse")
    decision = await service_desk.triage(session, inc, FakeLLM([_llm()]), settings=LIVE)
    assert decision.outcome == DecisionOutcome.SUGGESTED
    assert decision.output["applied"] is False
    assert inc.status == IncidentStatus.CLOSED


# --- olay servisi --------------------------------------------------------------


async def test_status_change_keeps_externally_set_priority(session):
    # İçe alınan Jira kaydı: öncelik uzaktan P1, etki/aciliyet varsayılan LOW
    inc = Incident(
        number="INC0000001", title="Jira'dan", description="", caller="",
        impact=Impact.LOW, urgency=Urgency.LOW, priority=Priority.P1, version=1,
    )
    svc.apply_sla(inc)
    session.add(inc)
    await session.flush()
    due = inc.resolve_due_at
    await svc.update_incident(session, inc.id, IncidentUpdate(status="in_progress"), "ali")
    assert inc.priority == Priority.P1
    assert sla.ensure_utc(inc.resolve_due_at) == sla.ensure_utc(due)


async def test_optimistic_lock_detects_stale_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lock.db'}")
    from serviszeka import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s0:
            inc = await svc.create_incident(
                s0, IncidentCreate(title="Yazıcı", caller="ali"), actor="ali"
            )
            await s0.commit()
            inc_id = inc.id
        async with maker() as a, maker() as b:
            stale = await svc.get_incident(a, inc_id)  # A sürüm 1'i görür
            assert stale.version == 1
            await svc.update_incident(b, inc_id, IncidentUpdate(title="B"), "b", 1)
            await b.commit()  # sürüm 2
            with pytest.raises(svc.VersionConflictError):
                await svc.update_incident(a, inc_id, IncidentUpdate(title="A"), "a", 1)
            assert stale.title == "B"
    finally:
        await engine.dispose()


# --- senkron -------------------------------------------------------------------


class _OneTicketConnector:
    system = ExternalSystem.SERVICENOW

    def __init__(self) -> None:
        self.ticket = RemoteTicket(
            external_id="r1", number="INC9001", title="Uzak", status=IncidentStatus.NEW,
            priority=Priority.P5, impact=Impact.LOW, urgency=Urgency.LOW,
            updated_at="2024-01-01 00:00:01",
        )

    async def list_updated_since(self, since):
        return [RemoteTicket(**self.ticket.__dict__)]

    async def get(self, external_id):
        return RemoteTicket(**self.ticket.__dict__)

    async def create(self, fields):  # pragma: no cover
        raise ConnectorError("yok")

    async def update(self, external_id, fields):
        raise ConnectorError("uzak sistem kapalı")


async def test_inbound_priority_change_recomputes_sla_and_timestamps(session):
    conn = _OneTicketConnector()
    engine = SyncEngine(session, [conn])
    await engine.pull(ExternalSystem.SERVICENOW)
    inc = (await session.scalars(select(Incident))).one()
    assert inc.priority == Priority.P5

    conn.ticket.priority = Priority.P1
    conn.ticket.impact = Impact.HIGH
    conn.ticket.urgency = Urgency.HIGH
    conn.ticket.status = IncidentStatus.RESOLVED
    conn.ticket.updated_at = "2024-01-01 00:00:02"
    await engine.pull(ExternalSystem.SERVICENOW)

    assert inc.priority == Priority.P1
    start = sla.ensure_utc(inc.created_at)
    assert sla.ensure_utc(inc.resolve_due_at) == start + timedelta(hours=4)
    assert sla.ensure_utc(inc.response_due_at) == start + timedelta(minutes=15)
    assert inc.responded_at is not None
    assert inc.resolved_at is not None


async def test_imported_in_progress_ticket_counts_as_responded(session):
    conn = _OneTicketConnector()
    conn.ticket.status = IncidentStatus.IN_PROGRESS
    await SyncEngine(session, [conn]).pull(ExternalSystem.SERVICENOW)
    inc = (await session.scalars(select(Incident))).one()
    assert inc.responded_at is not None


async def test_jira_incremental_query_never_skips_west_of_utc_users():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"issues": [], "isLast": True})

    conn = JiraSMConnector(
        "https://x.atlassian.net", "e", "t", project_key="SD",
        transport=httpx.MockTransport(handler),
    )
    await conn.list_updated_since("2024-05-01T10:00:00.000+0000")
    # JQL kullanıcının saat diliminde yorumlanır (UTC-12'ye kadar): 12 saat geri gidilir
    assert 'updated >= "2024/04/30 22:00"' in bodies[0]["jql"]


async def test_resolve_conflict_connector_error_returns_502(session):
    inc = await svc.create_incident(session, IncidentCreate(title="x", caller="y"), "y")
    link = ExternalLink(incident_id=inc.id, system=ExternalSystem.SERVICENOW, external_id="r1")
    session.add(link)
    await session.flush()
    ev = SyncEvent(link_id=link.id, system=ExternalSystem.SERVICENOW, direction="inbound",
                   status="conflict", payload={})
    session.add(ev)
    await session.commit()

    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    async def _connectors():
        yield {ExternalSystem.SERVICENOW: _OneTicketConnector()}

    app = FastAPI()
    app.include_router(integrations_api.router)
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[integrations_api.get_connectors] = _connectors
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        r = await client.post(f"/integrations/conflicts/{ev.id}/resolve?winner=local")
    assert r.status_code == 502, r.text


# --- API şemaları --------------------------------------------------------------


async def test_audit_and_sync_event_times_are_utc(session):
    await audit.record(session, actor="a", action="x", entity_type="t", entity_id="1")
    ev = SyncEvent(system=ExternalSystem.JIRA_SM, direction="inbound", status="ok", payload={})
    session.add(ev)
    await session.commit()
    session.expunge_all()
    row = (await session.scalars(select(AuditLog))).one()
    ev = (await session.scalars(select(SyncEvent))).one()
    assert AuditEntryOut.model_validate(row).model_dump_json().count("Z\"") == 1
    assert SyncEventOut.model_validate(ev).model_dump_json().count("Z\"") == 1


# --- bilgi bankası ---------------------------------------------------------------


async def test_article_update_with_null_title_is_ignored(session):
    art = await knowledge.create_article(session, title="VPN", body="Sertifika yenile")
    await knowledge.update_article(session, art.id, ArticleUpdate(title=None, body=None))
    await session.commit()
    assert art.title == "VPN" and art.body == "Sertifika yenile"


# --- denetim zinciri ----------------------------------------------------------------


async def test_audit_chain_cannot_fork(session):
    first = await audit.record(session, actor="a", action="x", entity_type="t", entity_id="1")
    await audit.record(session, actor="a", action="y", entity_type="t", entity_id="1")
    # Eşzamanlı ikinci yazıcı aynı önceki hash'i görmüş olsun
    fork = AuditLog(
        occurred_at=first.occurred_at, actor="b", action="z", entity_type="t", entity_id="2",
        details={}, prev_hash=first.hash, hash="f" * 64,
    )
    session.add(fork)
    with pytest.raises(IntegrityError):
        await session.flush()


# --- gizlilik ----------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Cep: 532 123 45 67", "Cep: 5321234567", "Cep: (532) 123-45-67"])
def test_masks_mobile_without_trunk_prefix(text):
    assert mask_pii(text) == "Cep: [TELEFON]"
