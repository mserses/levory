"""Nöbetçi (on-call) bildirimleri: kaçırılmaması gereken olaylar için anlık uyarı.

Kalıcı outbox (transactional outbox, `models.NotificationOutbox`):

- `stage` bildirimi `notification_outbox` satırı olarak kaydı oluşturan işlemle **aynı işlemde**
  yazar (yük PII maskeli). Rollback olursa satır da yoktur: kaydedilmeyen karar için nöbetçi
  uyarılmaz. Commit edilen satır süreç çökse bile kaybolmaz.
- `send_pending` (commit sonrası, istek içinde) bu oturumun commit edilmiş satırlarını hemen
  gönderir; `dispatch_due` (zamanlayıcı, `scheduler.py`) zamanı gelmiş tüm satırları gönderir.
- Gönderim **en az bir kez**: satır önce kısa bir işlemde kiralanır (`next_attempt_at` karşılaştırmalı
  güncelleme; aynı satırı iki gönderici aynı anda almaz), ardından işlem dışında gönderilir, sonuç
  ayrı bir işlemde yazılır. Çökme olursa kira süresi dolunca yeniden denenir; satırın
  `idempotency_key`'i alıcıya iletilir (`Idempotency-Key` başlığı) ki yinelemeyi ayıklayabilsin.
- Başarısız gönderim üstel geri çekilmeyle yeniden denenir (`SZ_NOTIFICATION_BACKOFF_SECONDS`,
  `SZ_NOTIFICATION_MAX_ATTEMPTS`; aşılınca `dead`). Her deneme denetime yazılır
  (`notification.sent` / `notification.failed` / `notification.dead`); `last_error` URL ve gizli
  bilgi taşımaz.
"""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

import httpx
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from serviszeka import audit
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import NotificationState
from serviszeka.models import NotificationOutbox

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0


class NotificationError(Exception):
    """Bildirim gönderilemedi."""


@runtime_checkable
class Notifier(Protocol):
    async def notify(self, event: str, payload: dict[str, Any]) -> None: ...


class LogNotifier:
    """Webhook tanımlı değilse bildirimi yalnızca loglar."""

    async def notify(
        self, event: str, payload: dict[str, Any], *, idempotency_key: str | None = None
    ) -> None:
        logger.warning("Nöbetçi bildirimi: %s %s (%s)", event, payload, idempotency_key or "-")


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

    async def notify(
        self, event: str, payload: dict[str, Any], *, idempotency_key: str | None = None
    ) -> None:
        body: dict[str, Any] = {"event": event, "payload": payload}
        headers = {}
        if idempotency_key:
            # En az bir kez gönderim: alıcı aynı anahtarlı yinelemeyi ayıklayabilir
            body["idempotency_key"] = idempotency_key
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                resp = await client.post(self.url, json=body, headers=headers)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise NotificationError(f"Nöbetçi bildirimi gönderilemedi: {exc}") from exc


def get_notifier(settings: Settings | None = None) -> Notifier:
    settings = settings or get_settings()
    if settings.oncall_webhook_url:
        return WebhookNotifier(settings.oncall_webhook_url)
    if settings.notifier_provider in ("teams", "log"):
        # Açık bildirim seçimi Slack'i dışlar (Teams'te nöbetçi kanalı yok: log)
        return LogNotifier()
    from serviszeka.connectors.slack import build_slack_oncall

    slack = build_slack_oncall(settings)
    if slack is not None:
        return slack
    return LogNotifier()


# --- outbox: aynı işlemde kalıcı kayıt, commit sonrası gönderim -------------------------------

_OUTBOX_KEY = "serviszeka.notification_outbox"
_FLUSHED_KEY = "serviszeka.notification_outbox.flushed"

DEFAULT_TARGET = "oncall"
# Gönderim sırasında satır bu süre kiralanır (çöken gönderici sonrası yeniden deneme gecikmesi)
CLAIM_SECONDS = 120
MAX_BACKOFF_SECONDS = 3600
MAX_ERROR_LENGTH = 500
DISPATCH_BATCH = 100
ACTOR = "system:notifications"


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
    # Kalıcı outbox satırı (stage doldurur)
    outbox_id: str | None = None
    idempotency_key: str | None = None
    target: str = DEFAULT_TARGET


def _now() -> datetime:
    return datetime.now(UTC)


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
    # Kaydedilmeyen karar için nöbetçi uyarılmaz (outbox satırı da işlemle birlikte geri alındı)
    box = sync_session.info.get(_OUTBOX_KEY)
    if box:
        box[:] = [item for item in box if item.committed]


def _mask(value: dict[str, Any]) -> dict[str, Any]:
    from serviszeka.tools.redaction import jsonable, redact_args

    return redact_args(jsonable(value or {}))


def stage(
    session: AsyncSession,
    item: PendingNotification,
    *,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> NotificationOutbox:
    """Bildirimi kalıcı outbox'a (aynı işlemde) ekler; gönderim commit sonrasına kalır.

    Yük ve ayrıntılar PII maskeli saklanır. `idempotency_key` verilmezse satıra özgü üretilir;
    verilirse tekildir (aynı anahtarla ikinci satır işlemi başarısız kılar — çağıran önce
    `exists` ile bakmalıdır).
    """
    item.payload = _mask(item.payload)
    item.details = _mask(item.details)
    row = NotificationOutbox(
        id=str(uuid4()),
        idempotency_key=idempotency_key or item.idempotency_key or f"ntf:{uuid4()}",
        event=item.event,
        target=item.target,
        payload=item.payload,
        actor=item.actor,
        entity_type=item.entity_type,
        entity_id=str(item.entity_id),
        details=item.details,
        state=NotificationState.PENDING,
        attempts=0,
        next_attempt_at=now or _now(),
    )
    session.add(row)
    item.outbox_id = row.id
    item.idempotency_key = row.idempotency_key
    _outbox(session.sync_session).append(item)
    return row


async def exists(session: AsyncSession, idempotency_key: str) -> bool:
    """Bu anahtarla outbox satırı var mı (tekilleştirme, ör. SLA uyarısı olay+durum başına)."""
    found = await session.scalar(
        select(NotificationOutbox.id).where(NotificationOutbox.idempotency_key == idempotency_key)
    )
    return found is not None


def pending(session: AsyncSession) -> list[PendingNotification]:
    """Bu oturumda eklenip henüz gönderilmemiş (commit edilmiş ya da edilmemiş) bildirimler."""
    return list(_outbox(session.sync_session))


def discard_after(session: AsyncSession, count: int) -> None:
    """Outbox'ı ilk `count` öğeye indirir (geri alınan kısmi işlemin bildirimleri gönderilmez).

    Satırların kendisi SAVEPOINT geri alınınca veritabanından da düşer; burada kalan nesneler de
    oturumdan çıkarılır.
    """
    box = _outbox(session.sync_session)
    for item in box[count:]:
        for obj in list(session.new):
            if isinstance(obj, NotificationOutbox) and obj.id == item.outbox_id:
                session.expunge(obj)
    del box[count:]


# --- gönderim ----------------------------------------------------------------------------------------

_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)\b(bearer|token|secret|password|parola|api[_-]?key|authorization)\b\s*[:=]?\s*\S+"
)


def safe_error(exc: BaseException) -> str:
    """Hata özeti: URL (webhook adresi gizli belirteç taşır) ve anahtar=değer gizlileri ayıklanır."""
    from serviszeka.privacy import mask_pii

    text = f"{type(exc).__name__}: {exc}"
    text = _URL_RE.sub("[URL]", text)
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)} ***", text)
    return mask_pii(text)[:MAX_ERROR_LENGTH]


def backoff(attempts: int, settings: Settings | None = None) -> timedelta:
    """`attempts` başarısız denemeden sonra bekleme: taban * 2^(n-1), en fazla 1 saat."""
    settings = settings or get_settings()
    base = float(settings.notification_backoff_seconds)
    return timedelta(seconds=min(base * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_SECONDS))


async def _call_notifier(
    notifier: Notifier, event_name: str, payload: dict[str, Any], key: str
) -> None:
    fn = notifier.notify
    try:
        accepts_key = "idempotency_key" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        accepts_key = False
    if accepts_key:
        await fn(event_name, payload, idempotency_key=key)
    else:
        await fn(event_name, payload)


async def _claim(session: AsyncSession, outbox_id: str, now: datetime) -> bool:
    """Satırı kiralar (karşılaştırmalı güncelleme) ve commit eder; başkası aldıysa False."""
    result = await session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == outbox_id,
            NotificationOutbox.state.in_([NotificationState.PENDING, NotificationState.FAILED]),
            NotificationOutbox.next_attempt_at <= now,
        )
        .values(
            next_attempt_at=now + timedelta(seconds=CLAIM_SECONDS),
            attempts=NotificationOutbox.attempts + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount == 1


async def deliver(
    session: AsyncSession,
    outbox_id: str,
    notifier: Notifier,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Tek satırı gönderir. Dönen: yazılan denetim eylemi; satır alınamadıysa None.

    Oturumda commit edilmemiş iş olmamalıdır (kira commit edilir). Gönderim işlem dışında yapılır.
    Hata asla yükseltilmez.
    """
    settings = settings or get_settings()
    now = now or _now()
    try:
        if not await _claim(session, outbox_id, now):
            return None
        row = await session.get(NotificationOutbox, outbox_id, populate_existing=True)
        if row is None:  # pragma: no cover - silinmiş satır
            await session.commit()
            return None
        event_name, payload, key = row.event, dict(row.payload or {}), row.idempotency_key
        attempts, base_details = row.attempts, dict(row.details or {})
        actor, entity_type, entity_id = row.actor, row.entity_type, row.entity_id
        await session.commit()  # okuma işlemini kapat: gönderim sırasında işlem tutulmaz
    except Exception:
        logger.exception("Bildirim kuyruğu satırı alınamadı: %s", outbox_id)
        await session.rollback()
        return None
    details = {
        **base_details,
        "event": event_name,
        "notifier": type(notifier).__name__,
        "attempt": attempts,
        "outbox_id": outbox_id,
    }
    error: str | None = None
    try:
        await _call_notifier(notifier, event_name, payload, key)
    except Exception as exc:  # bildirim hatası çağıranı asla durdurmamalı
        logger.exception("Nöbetçi bildirimi başarısız: %s", entity_id)
        error = safe_error(exc)
    if error is None:
        action, values = (
            "notification.sent",
            {
                "state": NotificationState.SENT,
                "sent_at": now,
                "last_error": None,
            },
        )
    elif attempts >= settings.notification_max_attempts:
        action, values = (
            "notification.dead",
            {
                "state": NotificationState.DEAD,
                "last_error": error,
            },
        )
        details["error"] = error
    else:
        retry_at = now + backoff(attempts, settings)
        action, values = (
            "notification.failed",
            {
                "state": NotificationState.FAILED,
                "last_error": error,
                "next_attempt_at": retry_at,
            },
        )
        details.update(error=error, next_attempt_at=retry_at.isoformat())
    try:
        await session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == outbox_id)
            .values(**values, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await audit.record(
            session,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
        await session.commit()
    except Exception:
        # Sonuç yazılamadı: kira dolunca yeniden denenir (en az bir kez; anahtar alıcıya gider)
        logger.exception("Bildirim sonucu/denetim kaydı yazılamadı: %s", entity_id)
        await session.rollback()
    return action


async def send_pending(
    session: AsyncSession,
    notifier: Notifier,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """Bu oturumun commit edilmiş bildirimlerini hemen gönderir (her biri için ayrı denetim işlemi).

    Commit'ten sonra çağrılmalıdır. Hata asla yükseltilmez (loglanır ve denetime yazılır).
    Gönderilemeyen satır kalıcıdır: zamanlayıcı (`dispatch_due`) geri çekilmeyle yeniden dener.
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
        # Açık işlemde bekleyen iş var: kira/denetim commit'i onu da commit ederdi; gönderme
        logger.warning("Commit edilmemiş değişiklik var; bildirimler commit sonrasına kaldı.")
        return []
    ready = list(box)
    box.clear()
    if session.in_transaction():
        await session.commit()  # yalnızca okuma içeren açık işlemi kapat
    actions: list[str] = []
    for item in ready:
        if item.outbox_id is None:  # pragma: no cover - stage her zaman doldurur
            continue
        action = await deliver(session, item.outbox_id, notifier, now=now, settings=settings)
        if action is not None:
            actions.append(action)
    return actions


async def dispatch_due(
    session: AsyncSession,
    notifier: Notifier,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    limit: int = DISPATCH_BATCH,
) -> list[str]:
    """Zamanı gelmiş (pending/failed, `next_attempt_at <= now`) commit edilmiş satırları gönderir.

    Zamanlayıcı işi (`scheduler.py`); temiz bir oturumla çağrılmalıdır. Dönen: denetim eylemleri.
    """
    now = now or _now()
    ids = list(
        (
            await session.scalars(
                select(NotificationOutbox.id)
                .where(
                    NotificationOutbox.state.in_(
                        [NotificationState.PENDING, NotificationState.FAILED]
                    ),
                    NotificationOutbox.next_attempt_at <= now,
                )
                .order_by(NotificationOutbox.next_attempt_at)
                .limit(limit)
            )
        ).all()
    )
    await session.commit()
    actions: list[str] = []
    for outbox_id in ids:
        action = await deliver(session, outbox_id, notifier, now=now, settings=settings)
        if action is not None:
            actions.append(action)
    return actions
