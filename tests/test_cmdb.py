"""CMDB: yapılandırma öğeleri, ilişkiler ve etki analizi (servis + API)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from _auth import AGENT, dev_headers
from pydantic import ValidationError
from sqlalchemy import select

from serviszeka import audit, db
from serviszeka.domain.enums import CIClass, CIEnvironment, CIRelationshipType
from serviszeka.main import app
from serviszeka.models import AuditLog
from serviszeka.services import cmdb
from serviszeka.services.cmdb_schemas import CICreate, CIUpdate

R = CIRelationshipType
CHANGE_MGR = dev_headers("mudur", "change_manager")
CAB = dev_headers("kurul", "cab")
REQUESTER = dev_headers("zeynep", "requester")
APPROVER_ONLY = dev_headers("sef.ali", "approver")


async def _ci(session, name, ci_class=CIClass.APPLICATION, criticality=3, **kw) -> str:
    ci = await cmdb.create_ci(
        session,
        CICreate(name=name, ci_class=ci_class, criticality=criticality, **kw),
        "mudur",
    )
    return ci.id


# --- CRUD -------------------------------------------------------------------


async def test_create_get_list_ci(session):
    ci = await cmdb.create_ci(
        session,
        CICreate(
            name="ERP-DB",
            ci_class=CIClass.DATABASE,
            environment=CIEnvironment.PROD,
            criticality=1,
            owner_group="dba",
            attributes={"engine": "postgres", "version": "16"},
            external_refs={"servicenow": "abc123"},
        ),
        "mudur",
    )
    await session.commit()
    assert ci.version == 1 and ci.status.value == "active"
    got = await cmdb.get_ci(session, ci.id)
    assert got is not None and got.attributes["engine"] == "postgres"
    assert got.external_refs == {"servicenow": "abc123"}
    await _ci(session, "Test-App", environment=CIEnvironment.TEST)
    prod = await cmdb.list_cis(session, environment=CIEnvironment.PROD)
    assert [c.name for c in prod] == ["ERP-DB"]
    found = await cmdb.list_cis(session, q="erp")
    assert [c.name for c in found] == ["ERP-DB"]
    actions = (await session.scalars(select(AuditLog.action))).all()
    assert actions.count("cmdb.ci.create") == 2
    assert await audit.verify_chain(session)


@pytest.mark.parametrize("criticality", [0, 5])
def test_criticality_range(criticality):
    with pytest.raises(ValidationError):
        CICreate(name="x", ci_class=CIClass.SERVER, criticality=criticality)


async def test_owner_group_must_be_taxonomy_key(session):
    with pytest.raises(cmdb.CMDBValidationError):
        await cmdb.create_ci(
            session, CICreate(name="x", ci_class=CIClass.SERVER, owner_group="yok"), "mudur"
        )


async def test_duplicate_name_in_same_environment_rejected(session):
    await _ci(session, "Web-01", environment=CIEnvironment.PROD)
    await _ci(session, "Web-01", environment=CIEnvironment.TEST)  # farklı ortam: serbest
    with pytest.raises(cmdb.CMDBValidationError):
        await _ci(session, "Web-01", environment=CIEnvironment.PROD)


async def test_update_ci_with_version(session):
    ci_id = await _ci(session, "App")
    ci = await cmdb.update_ci(session, ci_id, CIUpdate(criticality=2), "mudur", expected_version=1)
    assert ci.criticality == 2 and ci.version == 2
    with pytest.raises(cmdb.VersionConflictError):
        await cmdb.update_ci(session, ci_id, CIUpdate(criticality=1), "mudur", expected_version=1)
    with pytest.raises(cmdb.CINotFoundError):
        await cmdb.update_ci(session, "yok", CIUpdate(criticality=1), "mudur")


# --- ilişkiler ----------------------------------------------------------------


async def test_relationship_rules(session):
    a = await _ci(session, "A")
    b = await _ci(session, "B")
    rel = await cmdb.add_relationship(session, a, b, R.DEPENDS_ON, "mudur")
    assert rel.source_id == a and rel.target_id == b
    with pytest.raises(cmdb.CMDBValidationError):
        await cmdb.add_relationship(session, a, a, R.DEPENDS_ON, "mudur")  # öz döngü
    with pytest.raises(cmdb.DuplicateRelationshipError):
        await cmdb.add_relationship(session, a, b, R.DEPENDS_ON, "mudur")
    with pytest.raises(cmdb.CINotFoundError):
        await cmdb.add_relationship(session, a, "yok", R.DEPENDS_ON, "mudur")
    # Aynı çift, farklı tür: serbest
    await cmdb.add_relationship(session, a, b, R.CONNECTS_TO, "mudur")
    rels = await cmdb.list_relationships(session, b)
    assert len(rels) == 2
    await cmdb.remove_relationship(session, rel.id, "mudur")
    assert len(await cmdb.list_relationships(session, b)) == 1
    actions = (await session.scalars(select(AuditLog.action))).all()
    assert "cmdb.relationship.create" in actions and "cmdb.relationship.delete" in actions


# --- etki analizi -------------------------------------------------------------


@pytest.fixture
async def topology(session):
    """db <- app (depends_on), app -> server (runs_on), app part_of erp hizmeti, web -> app."""
    ids = {
        "db": await _ci(session, "ERP-DB", CIClass.DATABASE, 2),
        "server": await _ci(session, "srv-01", CIClass.SERVER, 3),
        "app": await _ci(session, "ERP-App", CIClass.APPLICATION, 3),
        "web": await _ci(session, "ERP-Web", CIClass.APPLICATION, 4),
        "erp": await _ci(session, "ERP Hizmeti", CIClass.SERVICE, 1),
        "lonely": await _ci(session, "Yalnız", CIClass.SERVER, 4),
    }
    await cmdb.add_relationship(session, ids["app"], ids["db"], R.DEPENDS_ON, "mudur")
    await cmdb.add_relationship(session, ids["app"], ids["server"], R.RUNS_ON, "mudur")
    await cmdb.add_relationship(session, ids["app"], ids["erp"], R.PART_OF, "mudur")
    await cmdb.add_relationship(session, ids["web"], ids["app"], R.DEPENDS_ON, "mudur")
    await session.commit()
    return ids


async def test_impact_traverses_upstream_dependents(session, topology):
    t = topology
    result = await cmdb.impact_analysis(session, [t["db"]])
    by_id = {a.ci.id: a for a in result.affected}
    assert set(by_id) == {t["app"], t["web"], t["erp"]}
    assert by_id[t["app"]].depth == 1 and by_id[t["app"]].path == [t["db"], t["app"]]
    assert by_id[t["web"]].depth == 2 and by_id[t["web"]].path == [t["db"], t["app"], t["web"]]
    assert by_id[t["erp"]].depth == 2 and by_id[t["erp"]].relationship == R.PART_OF
    # Değişen CI'ın bağımlılıkları (sunucu) etkilenmez
    assert t["server"] not in by_id
    assert result.highest_criticality == 1
    assert [s.id for s in result.business_services] == [t["erp"]]


async def test_impact_of_leaf_and_unknown(session, topology):
    result = await cmdb.impact_analysis(session, [topology["lonely"]])
    assert result.affected == [] and result.highest_criticality == 4
    assert result.business_services == []
    with pytest.raises(cmdb.CINotFoundError):
        await cmdb.impact_analysis(session, ["yok"])


async def test_impact_max_depth(session, topology):
    result = await cmdb.impact_analysis(session, [topology["db"]], max_depth=1)
    assert {a.ci.id for a in result.affected} == {topology["app"]}


async def test_impact_multiple_roots_not_reported_as_affected(session, topology):
    t = topology
    result = await cmdb.impact_analysis(session, [t["db"], t["app"]])
    by_id = {a.ci.id: a for a in result.affected}
    assert t["app"] not in by_id  # kök, etkilenen değil
    assert by_id[t["web"]].depth == 1 and by_id[t["web"]].path == [t["app"], t["web"]]


async def test_impact_cycle_safe(session):
    a = await _ci(session, "A", criticality=3)
    b = await _ci(session, "B", criticality=2)
    c = await _ci(session, "C", criticality=4)
    await cmdb.add_relationship(session, a, b, R.DEPENDS_ON, "mudur")
    await cmdb.add_relationship(session, b, c, R.DEPENDS_ON, "mudur")
    await cmdb.add_relationship(session, c, a, R.DEPENDS_ON, "mudur")
    result = await cmdb.impact_analysis(session, [a], max_depth=20)
    assert sorted((x.ci.name, x.depth) for x in result.affected) == [("B", 2), ("C", 1)]
    assert result.highest_criticality == 2


async def test_impact_rejects_bad_depth(session, topology):
    with pytest.raises(cmdb.CMDBValidationError):
        await cmdb.impact_analysis(session, [topology["db"]], max_depth=0)


# --- API ------------------------------------------------------------------------


@pytest.fixture
async def client(session):
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _post_ci(client, **kw):
    body = {"name": "ERP-DB", "ci_class": "database", "environment": "prod", "criticality": 2}
    body.update(kw)
    r = await client.post("/cmdb/cis", json=body, headers=CHANGE_MGR)
    assert r.status_code == 201, r.text
    return r.json()


async def test_api_roles(client):
    body = {"name": "x", "ci_class": "server"}
    assert (await client.post("/cmdb/cis", json=body, headers=AGENT)).status_code == 403
    assert (await client.post("/cmdb/cis", json=body, headers=CAB)).status_code == 403
    assert (await client.get("/cmdb/cis", headers=REQUESTER)).status_code == 403
    assert (await client.get("/cmdb/cis", headers=APPROVER_ONLY)).status_code == 403
    await _post_ci(client)
    for headers in (AGENT, CAB, CHANGE_MGR):
        r = await client.get("/cmdb/cis", headers=headers)
        assert r.status_code == 200 and len(r.json()) == 1


async def test_api_crud_relationships_and_impact(client):
    dbci = await _post_ci(client)
    appci = await _post_ci(client, name="ERP-App", ci_class="application", criticality=3)
    svc = await _post_ci(client, name="ERP", ci_class="service", criticality=1)
    r = await client.get(f"/cmdb/cis/{dbci['id']}", headers=AGENT)
    assert r.status_code == 200 and r.headers["etag"] == '"1"'
    assert (await client.get("/cmdb/cis/yok", headers=AGENT)).status_code == 404

    r = await client.patch(
        f"/cmdb/cis/{dbci['id']}",
        json={"owner_group": "dba"},
        headers={**CHANGE_MGR, "If-Match": '"1"'},
    )
    assert r.status_code == 200 and r.json()["version"] == 2
    r = await client.patch(
        f"/cmdb/cis/{dbci['id']}", json={"criticality": 1}, headers={**CHANGE_MGR, "If-Match": '"1"'}
    )
    assert r.status_code == 409
    r = await client.patch(f"/cmdb/cis/{dbci['id']}", json={"owner_group": "yok"}, headers=CHANGE_MGR)
    assert r.status_code == 422

    rel = {"source_id": appci["id"], "target_id": dbci["id"], "type": "depends_on"}
    r = await client.post("/cmdb/relationships", json=rel, headers=CHANGE_MGR)
    assert r.status_code == 201, r.text
    assert (await client.post("/cmdb/relationships", json=rel, headers=CHANGE_MGR)).status_code == 409
    self_loop = {**rel, "target_id": appci["id"]}
    r = await client.post("/cmdb/relationships", json=self_loop, headers=CHANGE_MGR)
    assert r.status_code == 422
    part = {"source_id": appci["id"], "target_id": svc["id"], "type": "part_of"}
    assert (await client.post("/cmdb/relationships", json=part, headers=CHANGE_MGR)).status_code == 201

    r = await client.get(f"/cmdb/cis/{dbci['id']}/relationships", headers=AGENT)
    assert [x["type"] for x in r.json()] == ["depends_on"]

    r = await client.get(f"/cmdb/cis/{dbci['id']}/impact", headers=AGENT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert {a["ci"]["name"]: a["depth"] for a in data["affected"]} == {"ERP-App": 1, "ERP": 2}
    assert data["highest_criticality"] == 1
    assert [s["name"] for s in data["business_services"]] == ["ERP"]

    r = await client.post(
        "/cmdb/impact", json={"ci_ids": [dbci["id"]], "max_depth": 1}, headers=CAB
    )
    assert r.status_code == 200 and len(r.json()["affected"]) == 1
    r = await client.post("/cmdb/impact", json={"ci_ids": ["yok"]}, headers=CAB)
    assert r.status_code == 404

    rel_id = (await client.get(f"/cmdb/cis/{dbci['id']}/relationships", headers=AGENT)).json()[0]["id"]
    assert (await client.delete(f"/cmdb/relationships/{rel_id}", headers=AGENT)).status_code == 403
    assert (await client.delete(f"/cmdb/relationships/{rel_id}", headers=CHANGE_MGR)).status_code == 204
    assert (await client.delete(f"/cmdb/relationships/{rel_id}", headers=CHANGE_MGR)).status_code == 404
