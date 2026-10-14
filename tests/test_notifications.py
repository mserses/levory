"""Nöbetçi bildirim katmanı testleri (gerçek ağ yok, httpx.MockTransport)."""

import json
import logging

import httpx
import pytest

from serviszeka import notifications
from serviszeka.config import Settings


async def test_log_notifier_logs_event(caplog):
    caplog.set_level(logging.INFO, logger="serviszeka.notifications")
    await notifications.LogNotifier().notify("incident.p1", {"number": "INC0000001"})
    assert "incident.p1" in caplog.text and "INC0000001" in caplog.text


async def test_webhook_notifier_posts_json():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    notifier = notifications.WebhookNotifier(
        "https://oncall.example/hook", transport=httpx.MockTransport(handler)
    )
    await notifier.notify("incident.p1", {"number": "INC0000001", "priority": "P1"})

    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://oncall.example/hook"
    assert json.loads(req.content) == {
        "event": "incident.p1",
        "payload": {"number": "INC0000001", "priority": "P1"},
    }


async def test_webhook_notifier_raises_on_http_error():
    notifier = notifications.WebhookNotifier(
        "https://oncall.example/hook",
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    with pytest.raises(notifications.NotificationError):
        await notifier.notify("incident.p1", {})


async def test_webhook_notifier_raises_on_transport_error():
    def boom(request):
        raise httpx.ConnectError("bağlantı yok")

    notifier = notifications.WebhookNotifier(
        "https://oncall.example/hook", transport=httpx.MockTransport(boom)
    )
    with pytest.raises(notifications.NotificationError):
        await notifier.notify("incident.p1", {})


def test_get_notifier_selects_by_settings():
    assert isinstance(notifications.get_notifier(Settings()), notifications.LogNotifier)
    n = notifications.get_notifier(Settings(oncall_webhook_url="https://oncall.example/hook"))
    assert isinstance(n, notifications.WebhookNotifier)
    assert n.url == "https://oncall.example/hook"


def test_oncall_webhook_url_from_env(monkeypatch):
    monkeypatch.setenv("SZ_ONCALL_WEBHOOK_URL", "https://oncall.example/env")
    assert Settings().oncall_webhook_url == "https://oncall.example/env"


def test_implementations_satisfy_protocol():
    assert isinstance(notifications.LogNotifier(), notifications.Notifier)
    assert isinstance(notifications.WebhookNotifier("https://x"), notifications.Notifier)
