"""İki yönlü senkron motoru testleri (bellek içi sahte bağlayıcı)."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from serviszeka import audit
from serviszeka.domain.enums import ExternalSystem, Impact, IncidentStatus, Priority, Urgency
from serviszeka.integrations.base import RemoteTicket
from serviszeka.integrations.sync import SyncEngine, next_incident_number
from serviszeka.models import ExternalLink, Incident, SyncEvent


class FakeConnector:
    """Bellek içi ITSM: her yazmada updated_at artar."""

    def __init__(self, system: ExternalSystem = ExternalSystem.SERVICENOW) -> None:
        self.system = system
        self.store: dict[str, RemoteTicket] = {}
        self.clock = 0
        self.calls: list[tuple[str, Any]] = []

    def _tick(self) -> str:
        self.clock += 1
        return f"2024-01-01 00:00:{self.clock:02d}"

    def remote_edit(self, external_id: str, **changes: Any) -> None:
        t = self.store[external_id]
        for k, v in changes.items():
            setattr(t, k, v)
        t.updated_at = self._tick()

    async def get(self, external_id):
        self.calls.append(("get", external_id))
        t = self.store[external_id]
        return RemoteTicket(**t.__dict__)

    async def list_updated_since(self, since):
        self.calls.append(("list", since))
        return [RemoteTicket(**t.__dict__) for t in sorted(
            self.store.values(), key=lambda t: t.updated_at or "")
            if since is None or (t.updated_at or "") >= since]

    async def create(self, fields):
        self.calls.append(("create", fields))
        eid = f"r{len(self.store) + 1}"
        self.store[eid] = RemoteTicket(
            external_id=eid, number=f"INC90{len(self.store) + 1}", title=fields["title"],
            description=fields["description"], status=fields["status"],
            priority=fields["priority"], impact=fields["impact"], urgency=fields["urgency"],
            assignee=fields["assignee"], updated_at=self._tick(),
        )
        return await self.get(eid)

    async def update(self, external_id, fields):
        self.calls.append(("update", external_id))
        t = self.store[external_id]
        for k in ("title", "description", "status", "priority", "impact", "urgency", "assignee"):
            if k in fields:
                setattr(t, k, fields[k])
        t.updated_at = self._tick()
        return await self.get(external_id)

    def count(self, op: str) -> int:
        return sum(1 for c in self.calls if c[0] == op)


async def _incident(session, **kw) -> Incident:
    inc = Incident(
        number=await next_incident_number(session), title=kw.pop("title", "Sunucu yanıt vermiyor"),
        description="", impact=Impact.HIGH, urgency=Urgency.HIGH, priority=Priority.P1,
        version=1, **kw,
    )
    session.add(inc)
    await session.flush()
    return inc


async def _events(session, status=None):
    stmt = select(SyncEvent)
    if status:
        stmt = stmt.where(SyncEvent.status == status)
    return (await session.scalars(stmt)).all()


async def test_incident_number_helper(session):
    assert await next_incident_number(session) == "INC0000001"
    await _incident(session)
    assert await next_incident_number(session) == "INC0000002"


async def test_push_creates_remote_and_link(session):
    fake = FakeConnector()
    engine = SyncEngine(session, [fake])
    inc = await _incident(session)

    [ev] = await engine.push(inc)
    assert ev.status == "ok" and ev.direction == "outbound"
    assert fake.count("create") == 1
    link = (await session.scalars(select(ExternalLink))).one()
    assert link.incident_id == inc.id and link.external_id == "r1"
    assert link.last_synced_local_version == 1
    assert link.last_synced_remote_updated == fake.store["r1"].updated_at
    assert fake.store["r1"].impact == Impact.HIGH

    # Değişiklik yok -> atlanır
    [ev2] = await engine.push(inc)
    assert ev2.status == "skipped" and fake.count("update") == 0

    # Yerel değişiklik -> güncellenir
    inc.title = "Sunucu kapalı"
    inc.version += 1
    [ev3] = await engine.push(inc)
    assert ev3.status == "ok" and fake.store["r1"].title == "Sunucu kapalı"
    assert link.last_synced_local_version == 2
    assert await audit.verify_chain(session)


async def test_pull_creates_then_updates_local(session):
    fake = FakeConnector()
    fake.store["s1"] = RemoteTicket(
        external_id="s1", number="INC0010001", title="Uzak kayıt", description="d",
        status=IncidentStatus.NEW, priority=Priority.P3, impact=Impact.MEDIUM,
        urgency=Urgency.MEDIUM, updated_at="2024-01-01 00:00:00",
    )
    engine = SyncEngine(session, [fake])
    res = await engine.pull(ExternalSystem.SERVICENOW)
    assert res["created"] == 1 and res["cursor"] == "2024-01-01 00:00:00"

    inc = (await session.scalars(select(Incident))).one()
    assert inc.number == "INC0000001" and inc.title == "Uzak kayıt"
    assert inc.priority == Priority.P3 and inc.version == 1

    # İkinci çekme: imleç kullanılır, aynı kayıt tekrar gelir ama atlanır
    res = await engine.pull(ExternalSystem.SERVICENOW)
    assert fake.calls[-1] == ("list", "2024-01-01 00:00:00")
    assert res["skipped"] == 1 and res["updated"] == 0

    # Uzak güncelleme -> yerel güncellenir
    fake.remote_edit("s1", status=IncidentStatus.IN_PROGRESS, title="Uzak kayıt v2")
    res = await engine.pull(ExternalSystem.SERVICENOW)
    assert res["updated"] == 1
    await session.refresh(inc)
    assert inc.status == IncidentStatus.IN_PROGRESS and inc.title == "Uzak kayıt v2"
    assert inc.version == 2


async def test_no_echo_loop(session):
    fake = FakeConnector()
    engine = SyncEngine(session, [fake])
    inc = await _incident(session)
    await engine.push(inc)

    fake.remote_edit("r1", status=IncidentStatus.ON_HOLD)
    await engine.pull(ExternalSystem.SERVICENOW)
    assert inc.status == IncidentStatus.ON_HOLD and inc.version == 2

    # Gelen değişiklik geri gönderilmemeli
    [ev] = await engine.push(inc)
    assert ev.status == "skipped"
    assert fake.count("update") == 0

    # Kendi yazdığımız değişiklik de geri çekilmemeli
    inc.title = "Yerel düzenleme"
    inc.version += 1
    await engine.push(inc)
    res = await engine.pull(ExternalSystem.SERVICENOW)
    assert res["updated"] == 0 and res["skipped"] == 1
    assert inc.version == 3


async def test_conflict_detected_manual_then_resolved(session):
    fake = FakeConnector()
    engine = SyncEngine(session, [fake])
    inc = await _incident(session)
    await engine.push(inc)

    # İki taraf da değişiyor
    inc.title = "Yerel başlık"
    inc.version += 1
    fake.remote_edit("r1", title="Uzak başlık")

    res = await engine.pull(ExternalSystem.SERVICENOW)
    assert res["conflicts"] == 1
    assert inc.title == "Yerel başlık"  # üzerine yazılmadı
    [push_ev] = await engine.push(inc)
    assert push_ev.status == "conflict"
    assert fake.store["r1"].title == "Uzak başlık"  # uzak da ezilmedi
    conflicts = await engine.list_conflicts()
    assert len(conflicts) == 1  # aynı çakışma tekrar yazılmaz
    assert conflicts[0].payload["remote"]["title"] == "Uzak başlık"

    await engine.resolve_conflict(conflicts[0].id, "local", actor="user:ali")
    assert fake.store["r1"].title == "Yerel başlık"
    assert await engine.list_conflicts() == []
    # Çözümden sonra yankı yok
    assert (await engine.pull(ExternalSystem.SERVICENOW))["updated"] == 0
    assert (await engine.push(inc))[0].status == "skipped"


@pytest.mark.parametrize("policy", ["remote_wins", "local_wins"])
async def test_conflict_policies(session, policy):
    fake = FakeConnector()
    engine = SyncEngine(session, [fake], conflict_policy=policy)
    inc = await _incident(session)
    await engine.push(inc)
    inc.title = "Yerel"
    inc.version += 1
    fake.remote_edit("r1", title="Uzak")

    await engine.pull(ExternalSystem.SERVICENOW)
    assert await _events(session, "conflict") == []
    expected = "Uzak" if policy == "remote_wins" else "Yerel"
    assert inc.title == expected and fake.store["r1"].title == expected
    assert (await engine.push(inc))[0].status == "skipped"


async def test_every_sync_writes_event_and_errors_recorded(session):
    from serviszeka.integrations.base import ConnectorError

    class Broken(FakeConnector):
        async def create(self, fields):
            raise ConnectorError("bağlantı reddedildi")

    engine = SyncEngine(session, [Broken()])
    inc = await _incident(session)
    [ev] = await engine.push(inc)
    assert ev.status == "error" and "reddedildi" in ev.message
    await engine.pull(ExternalSystem.SERVICENOW)
    assert len(await _events(session)) == 2


async def test_api_status_pull_and_400(session):
    from serviszeka.api import integrations as api
    from serviszeka.db import get_session

    fake = FakeConnector()
    fake.store["s1"] = RemoteTicket(external_id="s1", number="INC1", title="API",
                                    updated_at="2024-01-01 00:00:00")
    app = FastAPI()
    app.include_router(api.router)

    async def _session():
        yield session

    async def _connectors():
        yield {ExternalSystem.SERVICENOW: fake}

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[api.get_connectors] = _connectors

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        r = await client.get("/integrations/status")
        assert {s["system"]: s["configured"] for s in r.json()} == {
            "servicenow": True, "jira_sm": False,
        }
        r = await client.post("/integrations/jira_sm/pull")
        assert r.status_code == 400 and "yapılandırılmamış" in r.json()["detail"]
        r = await client.post("/integrations/servicenow/pull")
        assert r.status_code == 200 and r.json()["created"] == 1
        inc = (await session.scalars(select(Incident))).one()
        inc.title = "değişti"
        inc.version += 1
        fake.remote_edit("s1", title="uzakta değişti")
        r = await client.post(f"/integrations/push/{inc.id}")
        assert r.json()[0]["status"] == "conflict"
        r = await client.get("/integrations/conflicts")
        [c] = r.json()
        r = await client.post(f"/integrations/conflicts/{c['id']}/resolve", params={"winner": "remote"})
        assert r.status_code == 200
        assert inc.title == "uzakta değişti"
