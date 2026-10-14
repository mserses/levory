"""ServiceNow CMDB çekimi: cmdb_ci / cmdb_rel_ci eşleme, sys_id ile upsert, artımlı çekim
(httpx.MockTransport; ağ çağrısı yok). Jira Assets yalnızca arayüz taslağıdır."""

import base64
from collections.abc import AsyncIterator

import httpx
import pytest
from _auth import ADMIN, AGENT, dev_headers
from sqlalchemy import select

from serviszeka import audit, db
from serviszeka.api import integrations as integrations_api
from serviszeka.domain.enums import (
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    CIStatus,
    ExternalSystem,
)
from serviszeka.integrations import jira_assets
from serviszeka.integrations.base import ConnectorError
from serviszeka.integrations.cmdb_base import CMDBSourceConnector
from serviszeka.integrations.servicenow_cmdb import (
    CI_TABLE_PATH,
    ServiceNowCMDBConnector,
    pull_cmdb,
    record_to_ci,
    record_to_relationship,
)
from serviszeka.main import app
from serviszeka.models import AuditLog, CIRelationship, CMDBExternalRef, ConfigurationItem
from serviszeka.services import cmdb
from serviszeka.services.cmdb_schemas import CICreate

R = CIRelationshipType


def _f(value, display=None):
    return {"value": value, "display_value": display if display is not None else value}


def _ci(sys_id, name, cls="cmdb_ci_appl", updated="2026-10-01 10:00:00", **kw):
    rec = {
        "sys_id": _f(sys_id),
        "name": _f(name),
        "sys_class_name": _f(cls),
        "install_status": _f("1", "Installed"),
        "used_for": _f("Production"),
        "short_description": _f("Açıklama"),
        "support_group": _f("grp1", "DBA Ekibi"),
        "sys_updated_on": _f(updated),
    }
    rec.update(kw)
    return rec


def _rel(sys_id, parent, child, type_display, updated="2026-10-01 10:00:00"):
    return {
        "sys_id": _f(sys_id),
        "parent": _f(parent, "p"),
        "child": _f(child, "c"),
        "type": _f("t-" + sys_id, type_display),
        "sys_updated_on": _f(updated),
    }


class FakeServiceNow:
    """cmdb_ci ve cmdb_rel_ci tablolarını sysparm_query ile sunan sahte örnek."""

    def __init__(self, cis=(), rels=()):
        self.cis = list(cis)
        self.rels = list(rels)
        self.requests: list[httpx.Request] = []

    @staticmethod
    def _since(query: str) -> str | None:
        for part in query.split("^"):
            if part.startswith("sys_updated_on>="):
                return part.removeprefix("sys_updated_on>=")
        return None

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        rows = self.cis if req.url.path == CI_TABLE_PATH else self.rels
        since = self._since(req.url.params.get("sysparm_query", ""))
        if since:
            rows = [r for r in rows if r["sys_updated_on"]["value"] >= since]
        rows = sorted(rows, key=lambda r: r["sys_updated_on"]["value"])
        off = int(req.url.params.get("sysparm_offset", "0"))
        lim = int(req.url.params.get("sysparm_limit", "100"))
        return httpx.Response(200, json={"result": rows[off : off + lim]})

    def connector(self, **kw) -> ServiceNowCMDBConnector:
        return ServiceNowCMDBConnector(
            "https://acme.service-now.com/", "admin", "s3cret",
            transport=httpx.MockTransport(self.handler), **kw,
        )


# --- eşleme ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        ("cmdb_ci_service", CIClass.SERVICE),
        ("cmdb_ci_appl", CIClass.APPLICATION),
        ("cmdb_ci_linux_server", CIClass.SERVER),
        ("cmdb_ci_win_server", CIClass.SERVER),
        ("cmdb_ci_db_mssql_instance", CIClass.DATABASE),
        ("cmdb_ci_ip_router", CIClass.NETWORK_DEVICE),
        ("cmdb_ci_vm_instance", CIClass.CLOUD_RESOURCE),
    ],
)
def test_class_mapping(cls, expected):
    assert record_to_ci(_ci("s1", "x", cls)).ci_class == expected


def test_unknown_class_is_not_mapped():
    assert record_to_ci(_ci("s1", "x", "cmdb_ci_printer")) is None


def test_ci_field_mapping():
    remote = record_to_ci(
        _ci(
            "s1", "ERP", "cmdb_ci_service",
            busines_criticality=_f("1 - most critical"),
            install_status=_f("7", "Retired"),
            used_for=_f("Development"),
            support_group=_f("g1", "dba"),
        )
    )
    assert remote.external_id == "s1" and remote.name == "ERP"
    assert remote.criticality == 1
    assert remote.status == CIStatus.RETIRED
    assert remote.environment == CIEnvironment.DEV
    assert remote.owner_group == "dba"  # taksonomi anahtarı -> sahip ekip
    assert remote.updated_at == "2026-10-01 10:00:00"
    assert remote.attributes["servicenow_class"] == "cmdb_ci_service"

    plain = record_to_ci(
        {"sys_id": "s2", "name": "Web", "sys_class_name": "cmdb_ci_appl",
         "sys_updated_on": "2026-01-01 00:00:00"}
    )
    # Bilinmeyen: prod (ihtiyatlı), kritiklik 3, aktif; taksonomi dışı grup sahip ekip olmaz
    assert (plain.environment, plain.criticality, plain.status, plain.owner_group) == (
        CIEnvironment.PROD, 3, CIStatus.ACTIVE, None,
    )
    other_group = record_to_ci(_ci("s3", "x"))
    assert other_group.owner_group is None
    assert other_group.attributes["servicenow_support_group"] == "DBA Ekibi"


@pytest.mark.parametrize(
    ("display", "src_is_parent", "expected"),
    [
        ("Depends on::Used by", True, R.DEPENDS_ON),
        ("Runs on::Runs", True, R.RUNS_ON),
        ("Hosted on::Hosts", True, R.RUNS_ON),
        ("Connects to::Connected by", True, R.CONNECTS_TO),
        ("Contains::Contained by", False, R.PART_OF),
        ("Members::Member of", False, R.PART_OF),
    ],
)
def test_relationship_type_mapping(display, src_is_parent, expected):
    rel = record_to_relationship(_rel("r1", "P", "C", display))
    assert rel.type == expected
    assert (rel.source_external_id, rel.target_external_id) == (
        ("P", "C") if src_is_parent else ("C", "P")
    )


def test_unknown_relationship_type_is_unmapped():
    rel = record_to_relationship(_rel("r1", "P", "C", "Sends data to::Receives data from"))
    assert rel.type is None and rel.remote_type == "Sends data to::Receives data from"


# --- bağlayıcı ------------------------------------------------------------------------


async def test_connector_queries_selected_classes_incrementally_with_paging():
    sn = FakeServiceNow(
        cis=[_ci(f"s{i}", f"App{i}", updated=f"2026-10-01 10:00:0{i}") for i in range(5)]
    )
    c = sn.connector(page_size=2)
    got = await c.list_cis_updated_since("2026-10-01 10:00:01")
    await c.aclose()
    assert [r.external_id for r in got] == ["s1", "s2", "s3", "s4"]
    req = sn.requests[0]
    assert req.url.host == "acme.service-now.com" and req.url.path == "/api/now/table/cmdb_ci"
    query = req.url.params["sysparm_query"]
    assert "sys_updated_on>=2026-10-01 10:00:01" in query
    assert "sys_class_nameIN" in query and "cmdb_ci_service" in query
    assert query.endswith("ORDERBYsys_updated_on")
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"admin:s3cret").decode()
    assert len(sn.requests) == 3  # 2 + 2 + 0..1 sayfa


async def test_connector_relationships_and_errors():
    sn = FakeServiceNow(rels=[_rel("r1", "a", "b", "Depends on::Used by")])
    c = sn.connector()
    rels = await c.list_relationships_updated_since(None)
    assert sn.requests[0].url.path == "/api/now/table/cmdb_rel_ci"
    assert "sys_updated_on" not in sn.requests[0].url.params["sysparm_query"].split("ORDERBY")[0]
    assert [r.external_id for r in rels] == ["r1"]

    bad = ServiceNowCMDBConnector(
        "https://acme.service-now.com", "a", "b",
        transport=httpx.MockTransport(lambda req: httpx.Response(401, text="nope")),
    )
    with pytest.raises(ConnectorError):
        await bad.list_cis_updated_since(None)


def test_connectors_satisfy_protocol():
    assert isinstance(FakeServiceNow().connector(), CMDBSourceConnector)


# --- çekim (upsert) ---------------------------------------------------------------------


def _topology():
    return FakeServiceNow(
        cis=[
            _ci("svc1", "Bordro", "cmdb_ci_service", busines_criticality=_f("1 - most critical")),
            _ci("app1", "Bordro-App", "cmdb_ci_appl"),
            _ci("db1", "Bordro-DB", "cmdb_ci_db_ora_instance"),
            _ci("prn1", "Yazici", "cmdb_ci_printer"),  # seçili sınıf değil (sahte örnek döndürse de)
        ],
        rels=[
            _rel("r1", "svc1", "app1", "Depends on::Used by"),
            _rel("r2", "app1", "db1", "Depends on::Used by"),
            _rel("r3", "app1", "prn1", "Depends on::Used by"),  # bilinmeyen CI -> atlanır
            _rel("r4", "app1", "db1", "Sends data to::Receives data from"),  # bilinmeyen tür
        ],
    )


async def _ci_by_ref(session, sys_id) -> ConfigurationItem:
    ref = await session.scalar(
        select(CMDBExternalRef).where(
            CMDBExternalRef.entity_type == "ci", CMDBExternalRef.external_id == sys_id
        )
    )
    return await session.get(ConfigurationItem, ref.entity_id)


async def test_pull_creates_cis_relationships_and_refs(session):
    sn = _topology()
    c = sn.connector()
    result = await pull_cmdb(session, c, actor="yonetici")
    await session.commit()

    # Seçili olmayan sınıf (yazıcı) bağlayıcıda süzülür, hiç sayılmaz
    assert result["cis"] == {"created": 3, "updated": 0, "unchanged": 0, "skipped": 0}
    assert result["relationships"] == {"created": 2, "updated": 0, "unchanged": 0, "skipped": 2}
    svc = await _ci_by_ref(session, "svc1")
    assert svc.ci_class == CIClass.SERVICE and svc.criticality == 1
    assert svc.external_refs == {"servicenow": "svc1"}
    app_ = await _ci_by_ref(session, "app1")
    db_ = await _ci_by_ref(session, "db1")
    rels = {
        (r.source_id, r.target_id, r.type)
        for r in (await session.scalars(select(CIRelationship))).all()
    }
    assert rels == {(svc.id, app_.id, R.DEPENDS_ON), (app_.id, db_.id, R.DEPENDS_ON)}

    # Etki analizi içe alınan topoloji üzerinde çalışır
    impact = await cmdb.impact_analysis(session, [db_.id])
    assert [s.id for s in impact.business_services] == [svc.id]

    actions = (await session.scalars(select(AuditLog.action))).all()
    assert actions.count("cmdb.ci.create") == 3
    assert "integration.cmdb_pull" in actions
    assert await audit.verify_chain(session)
    assert result["errors"] == []


async def test_pull_is_incremental_and_idempotent(session):
    sn = _topology()
    c = sn.connector()
    await pull_cmdb(session, c, actor="yonetici")
    await session.commit()
    sn.requests.clear()

    # Değişiklik yok: filigran (en son sys_updated_on) ile >= sorgusu, sürüm artmaz
    result = await pull_cmdb(session, c, actor="yonetici")
    assert result["since"] == {
        "ci": "2026-10-01 10:00:00",
        "relationship": "2026-10-01 10:00:00",
    }
    assert "sys_updated_on>=2026-10-01 10:00:00" in sn.requests[0].url.params["sysparm_query"]
    assert result["cis"]["created"] == 0 and result["cis"]["updated"] == 0
    assert result["relationships"]["created"] == 0
    app_ = await _ci_by_ref(session, "app1")
    assert app_.version == 1

    # Uzakta güncelleme: ad + kritiklik + emekli; yalnızca yeni kayıt gelir
    sn.cis.append(
        _ci("app1", "Bordro-Uygulama", "cmdb_ci_appl", updated="2026-10-02 09:00:00",
            busines_criticality=_f("2 - somewhat critical"), install_status=_f("7", "Retired"))
    )
    sn.cis = [r for r in sn.cis if not (r["sys_id"]["value"] == "app1" and
                                        r["sys_updated_on"]["value"] < "2026-10-02")]
    result = await pull_cmdb(session, c, actor="yonetici")
    await session.commit()
    assert result["cis"]["updated"] == 1
    app_ = await _ci_by_ref(session, "app1")
    assert (app_.name, app_.criticality, app_.status, app_.version) == (
        "Bordro-Uygulama", 2, CIStatus.RETIRED, 2,
    )
    refs = (await session.scalars(select(CMDBExternalRef))).all()
    assert len([r for r in refs if r.entity_type == "ci"]) == 3  # yinelenen eşleme yok

    # Tam çekim filigranı yok sayar
    sn.requests.clear()
    result = await pull_cmdb(session, c, actor="yonetici", full=True)
    assert "sys_updated_on" not in sn.requests[0].url.params["sysparm_query"].split("ORDERBY")[0]
    assert result["since"] == {"ci": None, "relationship": None}


async def test_pull_keeps_local_attributes_and_owner_when_unmapped(session):
    local = await cmdb.create_ci(
        session,
        CICreate(name="Bordro-App", ci_class=CIClass.APPLICATION, owner_group="business_apps",
                 attributes={"yerel": "x"}),
        "mudur",
    )
    sn = FakeServiceNow(cis=[_ci("app1", "Bordro-App", "cmdb_ci_appl")])
    result = await pull_cmdb(session, sn.connector(), actor="yonetici")
    # Aynı ad + ortamdaki eşlenmemiş yerel CI sahiplenilir (yinelenen CI açılmaz)
    assert result["cis"]["created"] == 0 and result["cis"]["updated"] == 1
    got = await _ci_by_ref(session, "app1")
    assert got.id == local.id
    assert got.owner_group == "business_apps"
    assert got.attributes["yerel"] == "x" and got.attributes["servicenow_class"] == "cmdb_ci_appl"
    assert got.external_refs == {"servicenow": "app1"}


async def test_pull_reports_name_conflict_with_other_remote_ci(session):
    await cmdb.create_ci(
        session,
        CICreate(name="Bordro-App", ci_class=CIClass.APPLICATION,
                 external_refs={"servicenow": "baska"}),
        "mudur",
    )
    sn = FakeServiceNow(
        cis=[_ci("app1", "Bordro-App"), _ci("app2", "Diger-App")],
        rels=[_rel("r1", "app1", "app2", "Depends on::Used by")],
    )
    result = await pull_cmdb(session, sn.connector(), actor="yonetici")
    assert result["cis"]["created"] == 1 and result["cis"]["skipped"] == 1
    assert result["errors"][0]["external_id"] == "app1"
    assert result["errors"][0]["entity_type"] == "ci"
    assert result["relationships"]["skipped"] == 1


async def test_pull_relationship_type_change_replaces_local_relationship(session):
    sn = FakeServiceNow(
        cis=[_ci("app1", "A"), _ci("srv1", "S", "cmdb_ci_linux_server")],
        rels=[_rel("r1", "app1", "srv1", "Depends on::Used by")],
    )
    c = sn.connector()
    await pull_cmdb(session, c, actor="yonetici")
    sn.rels = [_rel("r1", "app1", "srv1", "Runs on::Runs", updated="2026-10-03 00:00:00")]
    result = await pull_cmdb(session, c, actor="yonetici")
    assert result["relationships"]["updated"] == 1
    types = (await session.scalars(select(CIRelationship.type))).all()
    assert types == [R.RUNS_ON]


async def test_pull_connector_error_propagates(session):
    c = ServiceNowCMDBConnector(
        "https://acme.service-now.com", "a", "b",
        transport=httpx.MockTransport(lambda req: httpx.Response(500, text="boom")),
    )
    with pytest.raises(ConnectorError):
        await pull_cmdb(session, c, actor="yonetici")


# --- Jira Assets: yalnızca arayüz taslağı ---------------------------------------------


async def test_jira_assets_is_interface_stub_only():
    stub = jira_assets.JiraAssetsCMDBConnector()
    assert stub.system == ExternalSystem.JIRA_SM
    with pytest.raises(NotImplementedError):
        await stub.list_cis_updated_since(None)
    with pytest.raises(NotImplementedError):
        await stub.list_relationships_updated_since(None)


# --- API ------------------------------------------------------------------------------


@pytest.fixture
async def ctx(session):
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    state = {"sn": _topology()}

    async def _connector():
        yield state["sn"].connector() if state["sn"] is not None else None

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[integrations_api.get_servicenow_cmdb] = _connector
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN
    ) as client:
        yield client, state, session
    app.dependency_overrides.clear()


async def test_api_cmdb_pull_admin_only(ctx):
    client, _state, session = ctx
    for headers in (AGENT, dev_headers("mudur", "change_manager")):
        r = await client.post("/integrations/servicenow/cmdb/pull", headers=headers)
        assert r.status_code == 403
    r = await client.post("/integrations/servicenow/cmdb/pull")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["system"] == "servicenow" and body["cis"]["created"] == 3
    assert body["full"] is False
    names = {c.name for c in (await session.scalars(select(ConfigurationItem))).all()}
    assert names == {"Bordro", "Bordro-App", "Bordro-DB"}

    r = await client.post("/integrations/servicenow/cmdb/pull", params={"full": "true"})
    assert r.status_code == 200 and r.json()["full"] is True


async def test_api_cmdb_pull_not_configured_and_remote_error(ctx):
    client, state, _ = ctx
    state["sn"] = None
    r = await client.post("/integrations/servicenow/cmdb/pull")
    assert r.status_code == 400 and "yapılandırılmamış" in r.json()["detail"]

    class Broken(FakeServiceNow):
        def handler(self, req):
            return httpx.Response(503, text="bakımda")

    state["sn"] = Broken()
    r = await client.post("/integrations/servicenow/cmdb/pull")
    assert r.status_code == 502
