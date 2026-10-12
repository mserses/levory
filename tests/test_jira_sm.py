"""Jira Service Management bağlayıcısı testleri (httpx.MockTransport)."""

import base64
import json

import httpx
import pytest

from serviszeka.domain.enums import IncidentStatus, Priority
from serviszeka.integrations.base import ConnectorError
from serviszeka.integrations.jira_sm import (
    JiraSMConnector,
    adf_to_text,
    text_to_adf,
    to_jql_datetime,
)

BASE = "https://acme.atlassian.net"


def _issue(key="SD-1", status="Waiting for support", priority="High", updated="2024-05-01T10:00:00.000+0000"):
    return {
        "key": key,
        "fields": {
            "summary": "E-posta gelmiyor",
            "description": text_to_adf("Satır 1\nSatır 2"),
            "status": {"name": status, "statusCategory": {"key": "new"}},
            "priority": {"name": priority},
            "assignee": {"displayName": "Mehmet Kaya"},
            "updated": updated,
        },
    }


def _connector(handler, **kw) -> JiraSMConnector:
    kw.setdefault("service_desk_id", "4")
    return JiraSMConnector(BASE, "bot@acme.com", "tok", transport=httpx.MockTransport(handler), **kw)


def test_adf_roundtrip_and_nested_nodes():
    adf = text_to_adf("Merhaba\n\nDünya")
    assert adf["type"] == "doc" and adf["version"] == 1
    assert adf["content"][0] == {"type": "paragraph", "content": [{"type": "text", "text": "Merhaba"}]}
    assert adf_to_text(adf) == "Merhaba\n\nDünya"
    nested = {
        "type": "doc", "version": 1,
        "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Başlık"}]},
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "a"},
                                                      {"type": "hardBreak"},
                                                      {"type": "text", "text": "b"}]}]},
            ]},
        ],
    }
    assert adf_to_text(nested) == "Başlık\na\nb"
    assert adf_to_text(None) == ""


def test_jql_datetime():
    assert to_jql_datetime("2024-05-01T13:15:30.000+0300") == "2024/05/01 10:15"


async def test_get_maps_issue_and_basic_auth():
    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = req.url, req.headers["authorization"]
        return httpx.Response(200, json=_issue())

    t = await _connector(handler).get("SD-1")
    assert seen["url"].path == "/rest/api/3/issue/SD-1"
    assert "summary" in seen["url"].params["fields"]
    assert seen["auth"] == "Basic " + base64.b64encode(b"bot@acme.com:tok").decode()
    assert t.external_id == "SD-1" and t.number == "SD-1"
    assert t.description == "Satır 1\nSatır 2"
    assert t.status == IncidentStatus.NEW
    assert t.priority == Priority.P2
    assert t.assignee == "Mehmet Kaya"
    assert t.updated_at == "2024-05-01T10:00:00.000+0000"


@pytest.mark.parametrize(
    ("name", "cat", "expected"),
    [("In Progress", "indeterminate", IncidentStatus.IN_PROGRESS),
     ("Waiting for customer", "indeterminate", IncidentStatus.ON_HOLD),
     ("Resolved", "done", IncidentStatus.RESOLVED),
     ("Closed", "done", IncidentStatus.CLOSED),
     ("Canceled", "done", IncidentStatus.CANCELLED),
     ("Özel Durum", "indeterminate", IncidentStatus.IN_PROGRESS)],
)
async def test_status_mapping(name, cat, expected):
    issue = _issue(status=name)
    issue["fields"]["status"]["statusCategory"]["key"] = cat
    t = await _connector(lambda r: httpx.Response(200, json=issue)).get("SD-1")
    assert t.status == expected


async def test_create_via_servicedesk_then_set_priority():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        p = req.url.path
        if p == "/rest/servicedeskapi/servicedesk/4/requesttype":
            return httpx.Response(200, json={"values": [{"id": "17", "name": "Incident"}]})
        if p == "/rest/servicedeskapi/request":
            return httpx.Response(201, json={"issueId": "1001", "issueKey": "SD-9"})
        if p == "/rest/api/3/issue/SD-9" and req.method == "PUT":
            return httpx.Response(204)
        if p == "/rest/api/3/issue/SD-9":
            return httpx.Response(200, json=_issue("SD-9", priority="Highest"))
        raise AssertionError(p)

    t = await _connector(handler).create({
        "title": "E-posta gelmiyor", "description": "Detay", "status": IncidentStatus.NEW,
        "priority": Priority.P1,
    })
    methods_paths = [(m, p) for m, p, _ in calls]
    assert ("POST", "/rest/servicedeskapi/request") in methods_paths
    req_body = next(b for m, p, b in calls if p == "/rest/servicedeskapi/request")
    assert req_body == {
        "serviceDeskId": "4", "requestTypeId": "17",
        "requestFieldValues": {"summary": "E-posta gelmiyor", "description": "Detay"},
    }
    put_body = next(b for m, p, b in calls if m == "PUT")
    assert put_body == {"fields": {"priority": {"name": "Highest"}}}
    # NEW durumu için geçiş yapılmaz
    assert not any("transitions" in p for _, p, _ in calls)
    assert t.external_id == "SD-9" and t.priority == Priority.P1


async def test_update_sends_adf_and_transitions_by_status_name():
    calls = []
    state = {"status": "Waiting for support"}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        p = req.url.path
        if p == "/rest/api/3/issue/SD-1" and req.method == "PUT":
            return httpx.Response(204)
        if p == "/rest/api/3/issue/SD-1/transitions" and req.method == "GET":
            return httpx.Response(200, json={"transitions": [
                {"id": "11", "name": "Start progress", "to": {"name": "In Progress"}},
                {"id": "31", "name": "Resolve this issue", "to": {"name": "Resolved"}},
            ]})
        if p == "/rest/api/3/issue/SD-1/transitions" and req.method == "POST":
            state["status"] = "Resolved"
            return httpx.Response(204)
        if p == "/rest/api/3/issue/SD-1":
            return httpx.Response(200, json=_issue(status=state["status"]))
        raise AssertionError(p)

    t = await _connector(handler).update(
        "SD-1", {"title": "Yeni başlık", "description": "a\nb", "status": IncidentStatus.RESOLVED}
    )
    put_body = next(b for m, p, b in calls if m == "PUT")
    assert put_body["fields"]["summary"] == "Yeni başlık"
    assert put_body["fields"]["description"] == text_to_adf("a\nb")
    post = next(b for m, p, b in calls if m == "POST")
    assert post == {"transition": {"id": "31"}}
    assert t.status == IncidentStatus.RESOLVED


async def test_update_missing_transition_raises():
    def handler(req):
        if req.url.path.endswith("/transitions"):
            return httpx.Response(200, json={"transitions": []})
        return httpx.Response(200, json=_issue())

    with pytest.raises(ConnectorError):
        await _connector(handler).update("SD-1", {"status": IncidentStatus.CLOSED})


async def test_list_updated_since_uses_jql_and_paginates():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/servicedeskapi/servicedesk/4":
            return httpx.Response(200, json={"id": "4", "projectKey": "SD"})
        assert req.method == "POST" and req.url.path == "/rest/api/3/search/jql"
        body = json.loads(req.content)
        bodies.append(body)
        if "nextPageToken" not in body:
            return httpx.Response(200, json={"issues": [_issue("SD-1")], "nextPageToken": "p2", "isLast": False})
        return httpx.Response(200, json={"issues": [_issue("SD-2")], "isLast": True})

    tickets = await _connector(handler).list_updated_since("2024-05-01T10:00:00.000+0000")
    assert [t.external_id for t in tickets] == ["SD-1", "SD-2"]
    assert bodies[0]["jql"] == 'project = "SD" AND updated >= "2024/05/01 10:00" ORDER BY updated ASC'
    assert bodies[1]["nextPageToken"] == "p2"
    assert "updated" in bodies[0]["fields"]
