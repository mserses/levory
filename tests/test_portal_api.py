"""Web portalı için eklenen uçlar: CORS, requester rolü, ?mine, karar kutusu, /me, OpenAPI dışa aktarımı."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from _auth import ADMIN, AGENT, APPROVER, dev_headers

from serviszeka import db
from serviszeka.api import incidents as incidents_api
from serviszeka.config import Settings, get_settings
from serviszeka.llm import FakeLLM
from serviszeka.main import app

REQ_ZEYNEP = dev_headers("zeynep", "requester")
REQ_MEHMET = dev_headers("mehmet", "requester")


def _llm(**kw):
    data = {
        "kind": "incident",
        "category": "ag",
        "service": "vpn",
        "impact": "medium",
        "urgency": "medium",
        "assignment_group": "network_ops",
        "suggested_resolution": "Sertifikayı yenileyin.",
        "kb_article_ids": [],
        "confidence": 0.9,
        "rationale": "test",
    }
    data.update(kw)
    return data


@pytest.fixture
async def ctx(session):
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    # Gölge mod: triage kararları "suggested" olur (onay kutusuna düşer)
    state = {"llm": FakeLLM([_llm()]), "settings": Settings(ai_shadow_mode=True)}
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: state["llm"]
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: state["settings"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, state
    app.dependency_overrides.clear()


async def _create(client, headers=AGENT, **kw):
    body = {"title": "VPN bağlanamıyorum", "description": "Evden bağlantı yok", "caller": "ayse"}
    body.update(kw)
    r = await client.post("/incidents", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- /me ---


async def test_me_returns_username_and_sorted_roles(ctx):
    client, _ = ctx
    r = await client.get("/me", headers=dev_headers("ayse", "approver", "agent"))
    assert r.status_code == 200
    assert r.json() == {
        "username": "ayse",
        "roles": ["agent", "approver"],
        "ai_confidence_threshold": 0.75,
    }


async def test_me_reports_configured_confidence_threshold(ctx, monkeypatch):
    client, _ = ctx
    monkeypatch.setenv("SZ_AI_CONFIDENCE_THRESHOLD", "0.6")
    get_settings.cache_clear()
    r = await client.get("/me", headers=AGENT)
    assert r.json()["ai_confidence_threshold"] == 0.6


async def test_me_without_roles_is_allowed_but_requires_token(ctx):
    client, _ = ctx
    r = await client.get("/me", headers={"Authorization": "Bearer dev:kimse:"})
    assert r.status_code == 200
    assert r.json()["username"] == "kimse"
    assert r.json()["roles"] == []
    assert (await client.get("/me")).status_code == 401


# --- requester ---


async def test_requester_create_forces_caller_and_ignores_impact(ctx):
    client, _ = ctx
    inc = await _create(
        client, REQ_ZEYNEP, caller="baskasi", impact="high", urgency="high", title="Yazıcı"
    )
    assert inc["caller"] == "zeynep"
    assert inc["impact"] == "low"  # requester etki beyan edemez
    assert inc["urgency"] == "high"
    assert inc["ai_confidence"] is None


async def test_non_agent_creator_cannot_spoof_caller(ctx):
    """Yalnızca agent başkası adına kayıt açar; requester+approver da kendi adına açar."""
    client, _ = ctx
    both = dev_headers("onayci.zeynep", "requester", "approver")
    inc = await _create(client, both, caller="genel.mudur", impact="high", urgency="high")
    assert inc["caller"] == "onayci.zeynep"
    assert inc["impact"] == "low"
    assert inc["priority"] != "P1"
    # caller vermeden de açabilir (requester gibi)
    r = await client.post("/incidents", json={"title": "Şifre"}, headers=both)
    assert r.status_code == 201, r.text
    assert r.json()["caller"] == "onayci.zeynep"
    # agent (ve admin) başkası adına açabilir
    inc = await _create(client, ADMIN, caller="genel.mudur", impact="high")
    assert inc["caller"] == "genel.mudur"
    assert inc["impact"] == "high"


async def test_requester_may_omit_caller_but_agent_may_not(ctx):
    client, _ = ctx
    r = await client.post("/incidents", json={"title": "Şifre"}, headers=REQ_ZEYNEP)
    assert r.status_code == 201, r.text
    assert r.json()["caller"] == "zeynep"
    r = await client.post("/incidents", json={"title": "Şifre"}, headers=AGENT)
    assert r.status_code == 422
    assert "caller" in r.json()["detail"]


async def test_requester_sees_only_own_incidents(ctx):
    client, _ = ctx
    own = await _create(client, REQ_ZEYNEP, title="Benim kaydım")
    other = await _create(client, REQ_MEHMET, title="Mehmet'in kaydı")
    agent_made = await _create(client, AGENT, caller="zeynep", title="Agent açtı")

    r = await client.get("/incidents", headers=REQ_ZEYNEP)
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()}
    assert ids == {own["id"], agent_made["id"]}
    # mine=false gönderse bile yalnızca kendi kayıtları
    r = await client.get("/incidents", params={"mine": "false"}, headers=REQ_ZEYNEP)
    assert {i["id"] for i in r.json()} == ids

    r = await client.get(f"/incidents/{own['id']}", headers=REQ_ZEYNEP)
    assert r.status_code == 200
    assert r.headers["etag"] == '"1"'
    r = await client.get(f"/incidents/{other['id']}", headers=REQ_ZEYNEP)
    assert r.status_code == 404
    assert r.json()["detail"] == "Olay bulunamadı"


async def test_requester_cannot_see_ai_internals_or_modify(ctx):
    client, state = ctx
    own = await _create(client, REQ_ZEYNEP)
    # Agent triage başlatır; ai_confidence olaya yazılabilir ama requester görmez
    state["settings"] = Settings(ai_shadow_mode=False)
    assert (await client.post(f"/incidents/{own['id']}/triage", headers=AGENT)).status_code == 200
    agent_view = (await client.get(f"/incidents/{own['id']}", headers=AGENT)).json()
    assert agent_view["ai_confidence"] == pytest.approx(0.9)
    req_view = (await client.get(f"/incidents/{own['id']}", headers=REQ_ZEYNEP)).json()
    assert req_view["ai_confidence"] is None
    req_list = (await client.get("/incidents", headers=REQ_ZEYNEP)).json()
    assert all(i["ai_confidence"] is None for i in req_list)

    forbidden = [
        ("GET", f"/incidents/{own['id']}/decisions"),
        ("POST", f"/incidents/{own['id']}/triage"),
        ("PATCH", f"/incidents/{own['id']}"),
        ("GET", "/incidents/decisions"),
        ("GET", "/incidents/sla/alerts"),
        ("POST", "/incidents/decisions/x/approve"),
        ("GET", "/knowledge/articles"),
        ("GET", "/audit"),
    ]
    for method, path in forbidden:
        r = await client.request(method, path, headers=REQ_ZEYNEP, json={})
        assert r.status_code == 403, (method, path, r.status_code)


async def test_no_role_cannot_create(ctx):
    client, _ = ctx
    r = await client.post(
        "/incidents", json={"title": "x"}, headers={"Authorization": "Bearer dev:kimse:"}
    )
    assert r.status_code == 403


# --- ?mine=true ---


async def test_mine_filter_for_agent(ctx):
    client, _ = ctx
    mine = await _create(client, AGENT, caller="ayse", title="Benim")
    await _create(client, AGENT, caller="baskasi", title="Başkası")
    r = await client.get("/incidents", params={"mine": "true"}, headers=AGENT)
    assert [i["id"] for i in r.json()] == [mine["id"]]
    r = await client.get("/incidents", headers=AGENT)
    assert len(r.json()) == 2


# --- karar kutusu ---


async def test_decision_inbox(ctx):
    client, state = ctx
    a = await _create(client, AGENT, title="Birinci")
    b = await _create(client, AGENT, title="İkinci")
    c = await _create(client, AGENT, title="Üçüncü")
    state["llm"] = FakeLLM([_llm(), _llm(), _llm(confidence=0.2)])
    for inc in (a, b, c):
        r = await client.post(f"/incidents/{inc['id']}/triage", headers=AGENT)
        assert r.status_code == 200, r.text
    outcomes = {
        inc["id"]: (await client.get(f"/incidents/{inc['id']}/decisions", headers=AGENT)).json()[
            0
        ]["outcome"]
        for inc in (a, b, c)
    }
    assert outcomes[a["id"]] == "suggested"
    assert outcomes[c["id"]] == "escalated"

    r = await client.get("/incidents/decisions", headers=APPROVER)
    assert r.status_code == 200, r.text
    items = r.json()
    assert [i["incident_number"] for i in items] == [c["number"], b["number"], a["number"]]
    first = items[0]
    assert first["incident_title"] == "Üçüncü"
    assert first["incident_current_version"] == first["incident_version"]
    assert {"confidence", "rationale", "sources", "outcome"} <= set(first)

    r = await client.get(
        "/incidents/decisions", params={"outcome": "escalated"}, headers=APPROVER
    )
    assert [i["incident_id"] for i in r.json()] == [c["id"]]
    r = await client.get(
        "/incidents/decisions",
        params=[("outcome", "suggested"), ("outcome", "escalated")],
        headers=ADMIN,
    )
    assert len(r.json()) == 3
    r = await client.get("/incidents/decisions", params={"limit": 1, "offset": 1}, headers=ADMIN)
    assert [i["incident_id"] for i in r.json()] == [b["id"]]

    # Onaylanan karar kutudan düşer
    decision_a = next(i for i in items if i["incident_id"] == a["id"])
    r = await client.post(f"/incidents/decisions/{decision_a['id']}/approve", headers=APPROVER)
    assert r.status_code == 200, r.text
    r = await client.get("/incidents/decisions", headers=APPROVER)
    assert a["id"] not in {i["incident_id"] for i in r.json()}


async def test_decision_inbox_validation_and_roles(ctx):
    client, _ = ctx
    r = await client.get("/incidents/decisions", params={"outcome": "auto_applied"}, headers=ADMIN)
    assert r.status_code == 422
    assert (await client.get("/incidents/decisions", headers=AGENT)).status_code == 403
    assert (await client.get("/incidents/decisions")).status_code == 401


# --- CORS ---


@pytest.fixture
def cors_env(monkeypatch):
    def _set(value: str | None):
        if value is None:
            monkeypatch.delenv("SZ_CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("SZ_CORS_ORIGINS", value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def test_cors_origins_parsing(monkeypatch):
    assert Settings().cors_origins == []
    monkeypatch.setenv("SZ_CORS_ORIGINS", "http://localhost:5173, https://portal.ornek.com/")
    assert Settings().cors_origins == ["http://localhost:5173", "https://portal.ornek.com"]
    monkeypatch.setenv("SZ_CORS_ORIGINS", '["http://a.test"]')
    assert Settings().cors_origins == ["http://a.test"]


async def test_cors_disabled_by_default(ctx, cors_env):
    client, _ = ctx
    cors_env(None)
    r = await client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in r.headers


async def test_cors_allows_configured_origin(ctx, cors_env):
    client, _ = ctx
    cors_env("http://localhost:5173")
    r = await client.options(
        "/incidents",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,if-match,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "PATCH" in r.headers["access-control-allow-methods"]

    r = await client.get("/me", headers={**AGENT, "Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "etag" in r.headers["access-control-expose-headers"].lower()

    r = await client.get("/health", headers={"Origin": "http://kotu.example"})
    assert "access-control-allow-origin" not in r.headers


# --- OpenAPI dışa aktarımı ---


def test_openapi_export_contains_new_routes():
    from serviszeka.openapi_export import export

    doc = json.loads(export())
    assert "/me" in doc["paths"]
    assert "get" in doc["paths"]["/incidents/decisions"]
    params = {p["name"] for p in doc["paths"]["/incidents"]["get"]["parameters"]}
    assert "mine" in params
    assert "DecisionInboxItem" in doc["components"]["schemas"]
