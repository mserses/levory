"""Faz 3 MCP sunucusu (`/mcp`, resmi Python MCP SDK, streamable HTTP).

İstemci olarak SDK'nın kendi `Client`'ı kullanılır; HTTP katmanı ağ yerine uygulamaya
ASGI üzerinden bağlanır (gerçek ağ çağrısı yok).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import httpx2
import pytest
from _auth import dev_headers
from _tooling import LIVE, seed
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from serviszeka import auth
from serviszeka.main import app
from serviszeka.mcp_server import gateway
from serviszeka.models import ActionRequest, AuditLog
from serviszeka.tools import registry

AI_H = dev_headers("copilot", "ai_agent")


@pytest.fixture
def mcp_app(session, monkeypatch):
    monkeypatch.setattr(gateway, "settings_provider", lambda: LIVE)
    return gateway


@asynccontextmanager
async def mcp_client(headers: dict[str, str], mode: str = "auto") -> AsyncIterator[Client]:
    """SDK istemcisi; oturum yöneticisi (görev grubu) aynı görevde açılıp kapanır."""
    async with AsyncExitStack() as stack:
        if gateway._manager is None:
            await stack.enter_async_context(gateway.run())
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers)
        transport = streamable_http_client("http://test/mcp", http_client=http)
        yield await stack.enter_async_context(Client(transport, mode=mode))


def _payload(result) -> dict:
    assert result.content, result
    return json.loads(result.content[0].text)


async def test_mcp_requires_bearer_token(mcp_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
        r = await c.post("/mcp", json={}, headers={"Authorization": "Bearer bozuk"})
        assert r.status_code == 401


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_mcp_lists_every_tool_with_schema_and_risk(mcp_app, mode):
    async with mcp_client(AI_H, mode) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == set(registry.names())
    for name, tool in tools.items():
        spec = registry.get(name)
        assert f"[{spec.risk_class.value}" in tool.description, tool.description
        assert tool.input_schema["type"] == "object"
        props = set(tool.input_schema.get("properties", {}))
        if spec.risk_class.value == "R0":
            assert "dry_run" not in props
        else:
            assert {"dry_run", "idempotency_key"} <= props


async def test_mcp_lists_only_permitted_tools_for_humans(mcp_app):
    async with mcp_client(dev_headers("ayse", "agent")) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert "incident.get" in names and "audit.search" not in names
    async with mcp_client(dev_headers("son", "requester")) as c:
        assert (await c.list_tools()).tools == []


async def test_mcp_r0_result_is_pii_masked_for_ai(mcp_app, session):
    fx = await seed(session)
    async with mcp_client(AI_H) as c:
        res = await c.call_tool("incident.get", {"incident_id": fx.incident_id})
    assert not res.is_error
    body = _payload(res)
    assert body["status"] == "executed"
    desc = body["result"]["description"]
    assert "ali@ornek.com" not in desc and "[EPOSTA]" in desc and "[TELEFON]" in desc
    assert res.structured_content == body
    # İnsan aynı aracı maskesiz görür
    async with mcp_client(dev_headers("ayse", "agent")) as c:
        res = await c.call_tool("incident.get", {"incident_id": fx.incident_id})
    assert "ali@ornek.com" in _payload(res)["result"]["description"]


async def test_mcp_r2_returns_pending_approval(mcp_app, session):
    fx = await seed(session)
    args = {"incident_id": fx.incident_id, "resolution": "Güncellendi", "reason": "Kullanıcı onayı"}
    async with mcp_client(AI_H) as c:
        res = await c.call_tool("incident.resolve", args)
    body = _payload(res)
    assert body["status"] == "pending_approval" and body["action_request_id"]
    ar = await session.get(ActionRequest, body["action_request_id"])
    assert (
        ar.requested_by == "ai:copilot" and ar.state == "pending" and ar.reason == "Kullanıcı onayı"
    )


async def test_mcp_r1_policy_and_idempotency(mcp_app, session):
    fx = await seed(session)
    args = {
        "incident_id": fx.incident_id,
        "assignment_group": "network_ops",
        "confidence": 0.95,
        "idempotency_key": "mcp-1",
    }
    async with mcp_client(AI_H) as c:
        first = _payload(await c.call_tool("incident.assign", args))
        again = _payload(await c.call_tool("incident.assign", args))
        conflict = await c.call_tool(
            "incident.assign", {**args, "assignment_group": "service_desk_l1"}
        )
    assert first["status"] == "executed" and again["replayed"] is True
    assert conflict.is_error and _payload(conflict)["error"]["code"] == "idempotency_conflict"


async def test_mcp_enforces_roles_per_tool(mcp_app):
    async with mcp_client(dev_headers("ayse", "agent")) as c:
        res = await c.call_tool("audit.verify_chain", {})
    assert res.is_error and _payload(res)["error"]["code"] == "forbidden"
    async with mcp_client(dev_headers("yonetici", "admin")) as c:
        res = await c.call_tool("audit.verify_chain", {})
    assert _payload(res)["result"] == {"valid": True}


async def test_mcp_validation_and_unknown_tool_errors(mcp_app, session):
    fx = await seed(session)
    async with mcp_client(AI_H) as c:
        res = await c.call_tool("incident.resolve", {"incident_id": fx.incident_id})
        assert res.is_error and _payload(res)["error"]["code"] == "validation_error"
        res = await c.call_tool("incident.delete", {})
        assert res.is_error


async def test_mcp_dry_run(mcp_app, session):
    fx = await seed(session)
    async with mcp_client(AI_H) as c:
        res = await c.call_tool(
            "incident.resolve", {"incident_id": fx.incident_id, "resolution": "x", "dry_run": True}
        )
    body = _payload(res)
    assert body["status"] == "dry_run" and body["result"]["would"] == "pending_approval"
    assert (await session.scalars(select(ActionRequest))).all() == []


async def test_mcp_oidc_service_principal(session, monkeypatch):
    """OIDC: istemci kimlik bilgisi belirteci (azp) + ai_agent rolü -> ai:<istemci>."""
    from test_auth import FakeIdP, _settings

    idp = FakeIdP()
    idp.add_rsa("k1")
    settings = _settings()
    authenticator = auth.OIDCAuthenticator(settings, transport=idp.transport())
    monkeypatch.setattr(gateway, "settings_provider", lambda: LIVE)
    monkeypatch.setattr(gateway, "auth_settings_provider", lambda: settings)
    monkeypatch.setattr(gateway, "authenticator_provider", lambda s: authenticator)
    fx = await seed(session)
    token = idp.token(
        sub="5f1c", azp="servicedesk-copilot", roles=["ai_agent"],
        preferred_username=None, realm_access=None,
    )  # fmt: skip
    async with gateway.run():
        async with mcp_client({"Authorization": f"Bearer {token}"}) as c:
            body = _payload(
                await c.call_tool(
                    "incident.resolve", {"incident_id": fx.incident_id, "resolution": "x"}
                )
            )
        bad = idp.token(sub="x", roles=["ai_agent"], exp=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as h:
            r = await h.post("/mcp", json={}, headers={"Authorization": f"Bearer {bad}"})
            assert r.status_code == 401
    ar = await session.get(ActionRequest, body["action_request_id"])
    assert ar.requested_by == "ai:servicedesk-copilot"
    actors = set((await session.scalars(select(AuditLog.actor))).all())
    assert "ai:servicedesk-copilot" in actors
