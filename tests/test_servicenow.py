"""ServiceNow bağlayıcısı: URL, gövde ve eşleme testleri (httpx.MockTransport)."""

import base64
import json

import httpx
import pytest

from serviszeka.domain.enums import Impact, IncidentStatus, Priority, Urgency
from serviszeka.integrations.base import ConnectorError
from serviszeka.integrations.servicenow import ServiceNowConnector


def _rec(sys_id="abc123", state="2", updated="2024-05-01 10:00:00", **kw):
    rec = {
        "sys_id": {"value": sys_id, "display_value": sys_id},
        "number": {"value": "INC0010001", "display_value": "INC0010001"},
        "short_description": {"value": "VPN çalışmıyor", "display_value": "VPN çalışmıyor"},
        "description": {"value": "Bağlanamıyorum", "display_value": "Bağlanamıyorum"},
        "state": {"value": state, "display_value": "In Progress"},
        "impact": {"value": "1", "display_value": "1 - High"},
        "urgency": {"value": "2", "display_value": "2 - Medium"},
        "priority": {"value": "2", "display_value": "2 - High"},
        "assigned_to": {"value": "u1", "display_value": "Ayşe Yılmaz"},
        "sys_updated_on": {"value": updated, "display_value": updated},
    }
    rec.update(kw)
    return rec


def _connector(handler, **kw) -> ServiceNowConnector:
    return ServiceNowConnector(
        "https://acme.service-now.com/", "admin", "s3cret",
        transport=httpx.MockTransport(handler), **kw,
    )


async def test_get_maps_fields_and_uses_basic_auth():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = req.url
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"result": _rec()})

    c = _connector(handler)
    t = await c.get("abc123")
    await c.aclose()

    assert seen["url"].path == "/api/now/table/incident/abc123"
    assert seen["url"].host == "acme.service-now.com"
    assert seen["url"].params["sysparm_display_value"] == "all"
    assert seen["auth"] == "Basic " + base64.b64encode(b"admin:s3cret").decode()
    assert t.external_id == "abc123"
    assert t.number == "INC0010001"
    assert t.title == "VPN çalışmıyor"
    assert t.status == IncidentStatus.IN_PROGRESS
    assert t.impact == Impact.HIGH
    assert t.urgency == Urgency.MEDIUM
    assert t.priority == Priority.P2
    assert t.assignee == "Ayşe Yılmaz"
    assert t.updated_at == "2024-05-01 10:00:00"


@pytest.mark.parametrize(
    ("code", "status"),
    [("1", IncidentStatus.NEW), ("2", IncidentStatus.IN_PROGRESS), ("3", IncidentStatus.ON_HOLD),
     ("6", IncidentStatus.RESOLVED), ("7", IncidentStatus.CLOSED), ("8", IncidentStatus.CANCELLED)],
)
async def test_state_mapping(code, status):
    c = _connector(lambda req: httpx.Response(200, json={"result": _rec(state=code)}))
    assert (await c.get("abc123")).status == status


async def test_plain_value_records_supported():
    rec = {"sys_id": "x1", "number": "INC1", "short_description": "a", "state": "6",
           "impact": "3", "urgency": "3", "priority": "5", "sys_updated_on": "2024-01-01 00:00:00"}
    c = _connector(lambda req: httpx.Response(200, json={"result": rec}))
    t = await c.get("x1")
    assert (t.status, t.impact, t.urgency, t.priority) == (
        IncidentStatus.RESOLVED, Impact.LOW, Urgency.LOW, Priority.P5
    )


async def test_list_updated_since_uses_sysparm_query_and_paginates():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        offset = int(req.url.params["sysparm_offset"])
        if offset == 0:
            return httpx.Response(200, json={"result": [_rec("a"), _rec("b")]})
        return httpx.Response(200, json={"result": [_rec("c")]})

    c = _connector(handler, page_size=2)
    tickets = await c.list_updated_since("2024-05-01 09:00:00")
    assert [t.external_id for t in tickets] == ["a", "b", "c"]
    assert calls[0]["sysparm_query"] == "sys_updated_on>=2024-05-01 09:00:00^ORDERBYsys_updated_on"
    assert [c["sysparm_offset"] for c in calls] == ["0", "2"]
    assert calls[0]["sysparm_limit"] == "2"


async def test_list_without_cursor_has_no_filter():
    seen = {}

    def handler(req):
        seen.update(req.url.params)
        return httpx.Response(200, json={"result": []})

    assert await _connector(handler).list_updated_since(None) == []
    assert seen["sysparm_query"] == "ORDERBYsys_updated_on"


async def test_create_posts_mapped_payload():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"result": _rec(state="1")})

    c = _connector(handler)
    t = await c.create({
        "title": "Yazıcı arızası", "description": "Kat 3", "status": IncidentStatus.NEW,
        "impact": Impact.MEDIUM, "urgency": Urgency.HIGH, "priority": Priority.P2,
        "assignee": None,
    })
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/now/table/incident"
    assert seen["body"] == {
        "short_description": "Yazıcı arızası", "description": "Kat 3",
        "state": "1", "impact": "2", "urgency": "1",
    }
    assert t.status == IncidentStatus.NEW


async def test_update_patches_and_adds_close_info_on_resolve():
    seen = {}

    def handler(req):
        seen["method"], seen["path"] = req.method, req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"result": _rec(state="6")})

    c = _connector(handler)
    t = await c.update("abc123", {"status": IncidentStatus.RESOLVED, "resolution": "Yeniden başlatıldı"})
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/api/now/table/incident/abc123"
    assert seen["body"]["state"] == "6"
    assert seen["body"]["close_notes"] == "Yeniden başlatıldı"
    assert "close_code" in seen["body"]
    assert t.status == IncidentStatus.RESOLVED


async def test_http_error_raises_connector_error():
    c = _connector(lambda req: httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(ConnectorError):
        await c.get("abc123")
