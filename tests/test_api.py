"""Olay API'si uçtan uca testleri (serviszeka.main.app, bağımlılık geçersiz kılma ile)."""

from collections.abc import AsyncIterator

import httpx
import pytest

import serviszeka.llm as llm_module
from serviszeka import db, knowledge
from serviszeka.api import incidents as incidents_api
from serviszeka.config import Settings
from serviszeka.llm import FakeLLM
from serviszeka.main import app


def _llm_response(**kw):
    data = {
        "kind": "incident",
        "category": "network",
        "service": "VPN",
        "impact": "medium",
        "urgency": "high",
        "assignment_group": "Ağ Ekibi",
        "suggested_resolution": "Sertifikayı yenileyin.",
        "kb_article_ids": [],
        "confidence": 0.9,
        "rationale": "test",
    }
    data.update(kw)
    return data


@pytest.fixture
async def ctx(session):
    """session fikstürü bellek içi motoru kurar; her istek aynı motordan yeni oturum alır."""

    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    state = {"llm": FakeLLM([_llm_response()]), "settings": Settings(ai_shadow_mode=False)}
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: state["llm"]
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: state["settings"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, state, session
    app.dependency_overrides.clear()


async def _create(client, **kw):
    body = {"title": "VPN bağlanamıyorum", "description": "Evden bağlantı yok", "caller": "ayse"}
    body.update(kw)
    r = await client.post("/incidents", json=body, headers={"X-Actor": "ayse"})
    assert r.status_code == 201, r.text
    return r


async def test_create_get_list(ctx):
    client, _, _ = ctx
    r = await _create(client, impact="high", urgency="high")
    inc = r.json()
    assert inc["number"] == "INC0000001" and inc["priority"] == "P1"
    assert inc["version"] == 1 and r.headers["etag"] == '"1"'
    assert inc["response_due_at"] and inc["resolve_due_at"]
    await _create(client)

    r = await client.get(f"/incidents/{inc['id']}")
    assert r.status_code == 200 and r.json()["id"] == inc["id"]
    assert (await client.get("/incidents/yok")).status_code == 404

    r = await client.get("/incidents", params={"priority": "P1"})
    assert [i["number"] for i in r.json()] == ["INC0000001"]
    r = await client.get("/incidents", params={"status": "new"})
    assert len(r.json()) == 2
    assert (await client.post("/incidents", json={"title": "x"})).status_code == 422


async def test_patch_with_if_match_and_body_version(ctx):
    client, _, _ = ctx
    inc = (await _create(client)).json()
    url = f"/incidents/{inc['id']}"

    r = await client.patch(url, json={"assignee": "ali"}, headers={"If-Match": '"1"'})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert r.headers["etag"] == '"2"'

    # Eski sürümle başlık -> 409
    r = await client.patch(url, json={"assignee": "veli"}, headers={"If-Match": '"1"'})
    assert r.status_code == 409
    # Eski sürüm gövdede -> 409
    r = await client.patch(url, json={"assignee": "veli", "expected_version": 1})
    assert r.status_code == 409
    r = await client.patch(url, json={"assignee": "veli", "expected_version": 2})
    assert r.status_code == 200 and r.json()["assignee"] == "veli"

    # Geçersiz geçiş -> 422
    r = await client.patch(url, json={"status": "resolved"})
    assert r.status_code == 422
    r = await client.patch(url, json={"status": "resolved", "resolution": "Düzeldi"})
    assert r.status_code == 200 and r.json()["resolved_at"] and r.json()["responded_at"]
    assert (
        await client.patch(url, json={"title": "a"}, headers={"If-Match": "abc"})
    ).status_code == 400


async def test_triage_auto_apply_and_decisions(ctx):
    client, state, session = ctx
    kb = await knowledge.create_article(
        session, title="VPN bağlantı sorunu", body="VPN sertifikasını yenileyin."
    )
    await session.commit()
    state["llm"] = FakeLLM([_llm_response(kb_article_ids=[kb.id])])
    inc = (await _create(client)).json()

    r = await client.post(f"/incidents/{inc['id']}/triage")
    assert r.status_code == 200, r.text
    dec = r.json()
    assert dec["outcome"] == "auto_applied" and dec["sources"] == [kb.id]

    updated = (await client.get(f"/incidents/{inc['id']}")).json()
    assert updated["priority"] == "P2" and updated["assignment_group"] == "Ağ Ekibi"
    assert updated["version"] == 2

    r = await client.get(f"/incidents/{inc['id']}/decisions")
    assert [d["id"] for d in r.json()] == [dec["id"]]

    audit_resp = (await client.get("/audit/verify")).json()
    assert audit_resp == {"valid": True}


async def test_shadow_triage_then_approve(ctx):
    client, state, _ = ctx
    state["settings"] = Settings(ai_shadow_mode=True)
    inc = (await _create(client)).json()
    dec = (await client.post(f"/incidents/{inc['id']}/triage")).json()
    assert dec["outcome"] == "suggested"
    assert (await client.get(f"/incidents/{inc['id']}")).json()["version"] == 1

    url = f"/incidents/decisions/{dec['id']}/approve"
    assert (await client.post(url)).status_code == 400  # onaylayan yok
    r = await client.post(url, json={"approver": "sef.ali"})
    assert r.status_code == 200 and r.json()["approved_by"] == "sef.ali"
    after = (await client.get(f"/incidents/{inc['id']}")).json()
    assert after["category"] == "network" and after["version"] == 2
    assert (await client.post(url, headers={"X-Actor": "baska"})).status_code == 409
    assert (
        await client.post("/incidents/decisions/yok/approve", json={"approver": "a"})
    ).status_code == 404


async def test_triage_without_llm_config_returns_503(ctx, monkeypatch):
    client, _, _ = ctx
    del app.dependency_overrides[incidents_api.get_llm_client]
    orig = llm_module.get_llm
    monkeypatch.setattr(llm_module, "get_llm", lambda: orig(Settings(anthropic_api_key=None)))
    inc = (await _create(client)).json()
    r = await client.post(f"/incidents/{inc['id']}/triage")
    assert r.status_code == 503
    assert "SZ_ANTHROPIC_API_KEY" in r.json()["detail"]


async def test_sla_alerts(ctx):
    client, _, _ = ctx
    inc = (await _create(client, impact="high", urgency="high")).json()
    r = await client.get("/incidents/sla/alerts", params={"now": "2099-01-01T00:00:00Z"})
    assert r.status_code == 200
    alerts = [a for a in r.json() if a["incident_id"] == inc["id"]]
    assert {(a["metric"], a["state"]) for a in alerts} == {
        ("response", "breached"),
        ("resolve", "breached"),
    }
    r = await client.get("/incidents/sla/alerts")
    assert r.json() == []  # yeni açılmış olay henüz riskte değil
