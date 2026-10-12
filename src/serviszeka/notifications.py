"""Nöbetçi (on-call) bildirimleri: kaçırılmaması gereken olaylar için anlık uyarı."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from serviszeka.config import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0


class NotificationError(Exception):
    """Bildirim gönderilemedi."""


@runtime_checkable
class Notifier(Protocol):
    async def notify(self, event: str, payload: dict[str, Any]) -> None: ...


class LogNotifier:
    """Webhook tanımlı değilse bildirimi yalnızca loglar."""

    async def notify(self, event: str, payload: dict[str, Any]) -> None:
        logger.warning("Nöbetçi bildirimi: %s %s", event, payload)


class WebhookNotifier:
    """Bildirimi JSON olarak nöbetçi webhook'una POST eder."""

    def __init__(
        self,
        url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = url
        self._transport = transport
        self._timeout = timeout

    async def notify(self, event: str, payload: dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                resp = await client.post(self.url, json={"event": event, "payload": payload})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise NotificationError(f"Nöbetçi bildirimi gönderilemedi: {exc}") from exc


def get_notifier(settings: Settings | None = None) -> Notifier:
    settings = settings or get_settings()
    if settings.oncall_webhook_url:
        return WebhookNotifier(settings.oncall_webhook_url)
    return LogNotifier()
