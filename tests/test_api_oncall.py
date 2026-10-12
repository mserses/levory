"""Triage uç noktasının nöbetçi bildirim bağımlılığı testleri."""

import httpx

from serviszeka import db
from serviszeka.api import incidents as incidents_api
from serviszeka.config import Settings
from serviszeka.llm import FakeLLM
from serviszeka.main import app
from serviszeka.notifications import LogNotifier, WebhookNotifier


class _Recorder:
    def __init__(self):
        self.calls = []

    async def notify(self, event, payload):
        self.calls.append((event, payload))


def test_notifier_dependency_follows_settings():
    assert isinstance(incidents_api.get_oncall_notifier(Settings()), LogNotifier)
    n = incidents_api.get_oncall_notifier(Settings(oncall_webhook_url="https://oncall.example"))
    assert isinstance(n, WebhookNotifier)


async def test_triage_p1_raise_notifies_oncall(session):
    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    recorder = _Recorder()
    llm = FakeLLM(
        [
            {
                "kind": "incident",
                "impact": "high",
                "urgency": "high",
                "category": "ag",
                "confidence": 0.95,
            }
        ]
    )
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: llm
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: Settings(
        ai_shadow_mode=False
    )
    app.dependency_overrides[incidents_api.get_oncall_notifier] = lambda: recorder
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inc = (
                await client.post("/incidents", json={"title": "Tüm ağ çöktü", "caller": "noc"})
            ).json()
            r = await client.post(f"/incidents/{inc['id']}/triage")
            assert r.status_code == 200, r.text
            assert r.json()["outcome"] == "needs_approval"
            after = (await client.get(f"/incidents/{inc['id']}")).json()
            assert after["priority"] == "P5"
    finally:
        app.dependency_overrides.clear()
    assert [e for e, _ in recorder.calls] == ["incident.priority_raise_pending"]
    assert recorder.calls[0][1]["incident_number"] == inc["number"]
