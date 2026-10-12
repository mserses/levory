"""Nöbetçi (on-call) bildirimleri: kaçırılmaması gereken olaylar için anlık uyarı.

Bildirimler veritabanı işlemi içinde gönderilmez: `stage` ile oturumun outbox'ına eklenir,
yalnızca başarılı commit'ten sonra `send_pending` ile gönderilir. Rollback olursa atılır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from serviszeka import audit
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


# --- outbox: commit sonrası gönderim ---------------------------------------------

_OUTBOX_KEY = "serviszeka.notification_outbox"
_FLUSHED_KEY = "serviszeka.notification_outbox.flushed"


@dataclass
class PendingNotification:
    """Commit sonrası gönderilecek bildirim ve denetim kaydı bilgileri."""

    event: str
    payload: dict[str, Any]
    actor: str
    entity_type: str
    entity_id: str
    details: dict[str, Any] = field(default_factory=dict)
    # Kaydı oluşturan işlem commit edildi mi (yalnızca bunlar gönderilir)
    committed: bool = False


def _outbox(sync_session: Session) -> list[PendingNotification]:
    box = sync_session.info.get(_OUTBOX_KEY)
    if box is None:
        box = sync_session.info[_OUTBOX_KEY] = []
        event.listen(sync_session, "after_flush", _on_flush)
        event.listen(sync_session, "after_commit", _on_commit)
        event.listen(sync_session, "after_rollback", _on_rollback)
    return box


def _on_flush(sync_session: Session, _flush_context: Any) -> None:
    # Açık işlemde flush edilmiş (henüz commit edilmemiş) yazma var
    sync_session.info[_FLUSHED_KEY] = True


def _on_commit(sync_session: Session) -> None:
    sync_session.info.pop(_FLUSHED_KEY, None)
    for item in sync_session.info.get(_OUTBOX_KEY, ()):
        item.committed = True


def _on_rollback(sync_session: Session) -> None:
    sync_session.info.pop(_FLUSHED_KEY, None)
    # Kaydedilmeyen karar için nöbetçi uyarılmaz
    box = sync_session.info.get(_OUTBOX_KEY)
    if box:
        box[:] = [item for item in box if item.committed]


def stage(session: AsyncSession, item: PendingNotification) -> None:
    """Bildirimi oturumun outbox'ına ekler; gönderim commit sonrasına kalır."""
    _outbox(session.sync_session).append(item)


def pending(session: AsyncSession) -> list[PendingNotification]:
    """Henüz gönderilmemiş (commit edilmiş ya da edilmemiş) bildirimler."""
    return list(_outbox(session.sync_session))


async def send_pending(session: AsyncSession, notifier: Notifier) -> list[str]:
    """Commit edilmiş bildirimleri gönderir; her biri için kısa ayrı bir işlemde denetim yazar.

    Commit'ten sonra çağrılmalıdır. Hata asla yükseltilmez (loglanır ve denetime yazılır).
    Dönen liste: her bildirim için yazılan denetim eylemi.
    """
    box = _outbox(session.sync_session)
    uncommitted = (
        any(not item.committed for item in box)
        or session.sync_session.info.get(_FLUSHED_KEY)
        or session.new
        or session.dirty
        or session.deleted
    )
    if uncommitted:
        # Açık işlemde bekleyen iş var: denetim commit'i onu da commit ederdi; gönderme
        logger.warning("Commit edilmemiş değişiklik var; bildirimler commit sonrasına kaldı.")
        return []
    ready = list(box)
    box.clear()
    actions: list[str] = []
    for item in ready:
        details = {**item.details, "event": item.event, "notifier": type(notifier).__name__}
        try:
            await notifier.notify(item.event, item.payload)
            action = "notification.sent"
        except Exception as exc:  # bildirim hatası çağıranı asla durdurmamalı
            logger.exception("Nöbetçi bildirimi başarısız: %s", item.entity_id)
            action = "notification.failed"
            details["error"] = f"{type(exc).__name__}: {exc}"
        try:
            await audit.record(
                session,
                actor=item.actor,
                action=action,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                details=details,
            )
            await session.commit()
        except Exception:
            logger.exception("Bildirim denetim kaydı yazılamadı: %s", item.entity_id)
            await session.rollback()
        actions.append(action)
    return actions
