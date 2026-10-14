"""Kalıcı bildirim kuyruğu (notification_outbox): aynı işlemde yazım, en az bir kez gönderim,
üstel geri çekilme, ölü satır, idempotency anahtarı, gizli bilgisiz hata."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from serviszeka import audit, db, notifications
from serviszeka.config import Settings
from serviszeka.domain.enums import NotificationState
from serviszeka.models import AuditLog, NotificationOutbox

T0 = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
SETTINGS = Settings(notification_max_attempts=3, notification_backoff_seconds=10)


class Recorder:
    def __init__(self, fail: int = 0, error: Exception | None = None):
        self.calls: list[tuple[str, dict, str | None]] = []
        self.fail = fail
        self.error = error or RuntimeError("webhook kapalı")

    async def notify(self, event, payload, *, idempotency_key=None):
        self.calls.append((event, payload, idempotency_key))
        if self.fail:
            self.fail -= 1
            raise self.error


class LegacyRecorder:
    """Eski imza (idempotency anahtarı almayan bildirici) de desteklenir."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def notify(self, event, payload):
        self.calls.append((event, payload))


def _item(**kw) -> notifications.PendingNotification:
    data = {
        "event": "oncall.page",
        "payload": {"message": "Ali ali@ornek.com.tr aradı, 0532 123 45 67", "incident_id": "i-1"},
        "actor": "ayse",
        "entity_type": "incident",
        "entity_id": "i-1",
        "details": {"severity": "high"},
    }
    data.update(kw)
    return notifications.PendingNotification(**data)


async def _rows():
    async with db.get_sessionmaker()() as other:
        return list((await other.scalars(select(NotificationOutbox))).all())


async def _actions():
    async with db.get_sessionmaker()() as other:
        return [r.action for r in (await other.scalars(select(AuditLog).order_by(AuditLog.seq)))]


async def test_stage_writes_masked_row_in_same_transaction(session):
    row = notifications.stage(session, _item(), now=T0)
    await session.flush()
    # Aynı işlemde (kaydı oluşturanla birlikte) yazıldı; bellek içi SQLite tek bağlantı paylaştığı
    # için görünürlük ayrı oturumla değil, geri alma testleriyle sınanır
    assert await session.get(NotificationOutbox, row.id) is row
    await session.commit()
    [saved] = await _rows()
    assert saved.id == row.id and saved.state == NotificationState.PENDING
    assert saved.attempts == 0 and saved.target == "oncall" and saved.idempotency_key
    assert "ali@ornek.com.tr" not in str(saved.payload) and "[EPOSTA]" in saved.payload["message"]
    assert saved.payload["incident_id"] == "i-1"


async def test_rollback_leaves_no_row(session):
    notifications.stage(session, _item(), now=T0)
    await session.flush()
    await session.rollback()
    assert await _rows() == [] and notifications.pending(session) == []


async def test_savepoint_rollback_discards_row(session):
    count = len(notifications.pending(session))
    try:
        async with session.begin_nested():
            notifications.stage(session, _item(), now=T0)
            await session.flush()
            raise RuntimeError("işleyici hatası")
    except RuntimeError:
        notifications.discard_after(session, count)
    await session.commit()
    assert await _rows() == [] and notifications.pending(session) == []


async def test_committed_row_survives_crash_and_dispatcher_sends_it(session):
    notifications.stage(session, _item(), now=T0)
    await session.commit()
    # Süreç send_pending'den önce çöktü: yeni oturumla zamanlayıcı gönderir
    rec = Recorder()
    async with db.get_sessionmaker()() as fresh:
        out = await notifications.dispatch_due(fresh, rec, now=T0, settings=SETTINGS)
    assert out == ["notification.sent"]
    [row] = await _rows()
    assert row.state == NotificationState.SENT and row.attempts == 1
    assert rec.calls[0][2] == row.idempotency_key
    # Bir daha gönderilmez
    async with db.get_sessionmaker()() as fresh:
        assert await notifications.dispatch_due(fresh, rec, now=T0 + timedelta(hours=1)) == []
    assert len(rec.calls) == 1


async def test_send_pending_then_dispatcher_does_not_resend(session):
    notifications.stage(session, _item())
    await session.commit()
    rec = Recorder()
    assert await notifications.send_pending(session, rec) == ["notification.sent"]
    async with db.get_sessionmaker()() as fresh:
        assert await notifications.dispatch_due(fresh, rec, now=datetime.now(UTC)) == []
    assert len(rec.calls) == 1


async def test_failure_backs_off_exponentially_then_dead(session):
    notifications.stage(session, _item(), now=T0)
    await session.commit()
    rec = Recorder(fail=10)

    async def run(at):
        async with db.get_sessionmaker()() as s:
            return await notifications.dispatch_due(s, rec, now=at, settings=SETTINGS)

    assert await run(T0) == ["notification.failed"]
    [row] = await _rows()
    assert row.state == NotificationState.FAILED and row.attempts == 1
    assert row.next_attempt_at.replace(tzinfo=UTC) == T0 + timedelta(seconds=10)
    # Geri çekilme dolmadan yeniden denenmez
    assert await run(T0 + timedelta(seconds=9)) == []
    assert await run(T0 + timedelta(seconds=10)) == ["notification.failed"]
    [row] = await _rows()
    t2 = T0 + timedelta(seconds=10)
    assert row.attempts == 2 and row.next_attempt_at.replace(tzinfo=UTC) == t2 + timedelta(
        seconds=20
    )
    assert await run(t2 + timedelta(seconds=20)) == ["notification.dead"]
    [row] = await _rows()
    assert row.state == NotificationState.DEAD and row.attempts == 3
    assert await run(t2 + timedelta(days=1)) == []
    assert len(rec.calls) == 3
    acts = await _actions()
    assert acts.count("notification.failed") == 2 and acts.count("notification.dead") == 1
    async with db.get_sessionmaker()() as s:
        assert await audit.verify_chain(s)


async def test_recovers_after_transient_failure(session):
    notifications.stage(session, _item(), now=T0)
    await session.commit()
    rec = Recorder(fail=1)
    async with db.get_sessionmaker()() as s:
        assert await notifications.dispatch_due(s, rec, now=T0, settings=SETTINGS) == [
            "notification.failed"
        ]
        assert await notifications.dispatch_due(
            s, rec, now=T0 + timedelta(seconds=10), settings=SETTINGS
        ) == ["notification.sent"]
    [row] = await _rows()
    assert row.state == NotificationState.SENT and row.last_error is None
    # Aynı satır için iki deneme aynı idempotency anahtarını taşır
    assert rec.calls[0][2] == rec.calls[1][2] == row.idempotency_key


async def test_last_error_and_audit_have_no_secrets(session):
    notifications.stage(session, _item(), now=T0)
    await session.commit()
    secret_url = "https://hooks.slack.com/services/T000/B000/cokgizlibelirtec"
    err = httpx.HTTPStatusError(
        f"Server error '500' for url '{secret_url}' token=abc123",
        request=httpx.Request("POST", secret_url),
        response=httpx.Response(500),
    )
    async with db.get_sessionmaker()() as s:
        await notifications.dispatch_due(s, Recorder(fail=1, error=err), now=T0, settings=SETTINGS)
    [row] = await _rows()
    assert "cokgizlibelirtec" not in row.last_error and "abc123" not in row.last_error
    assert "[URL]" in row.last_error and "HTTPStatusError" in row.last_error
    async with db.get_sessionmaker()() as s:
        logs = await s.scalars(select(AuditLog).where(AuditLog.action == "notification.failed"))
        detail = str([log.details for log in logs])
    assert "cokgizlibelirtec" not in detail and "abc123" not in detail


async def test_claim_is_exclusive(session):
    row = notifications.stage(session, _item(), now=T0)
    await session.commit()
    async with db.get_sessionmaker()() as a, db.get_sessionmaker()() as b:
        assert await notifications._claim(a, row.id, T0) is True
        # İkinci gönderici aynı satırı kira süresince alamaz
        assert await notifications._claim(b, row.id, T0) is False
        later = T0 + timedelta(seconds=notifications.CLAIM_SECONDS)
        assert await notifications._claim(b, row.id, later) is True  # çöken gönderici sonrası


async def test_legacy_notifier_signature_supported(session):
    notifications.stage(session, _item(), now=T0)
    await session.commit()
    rec = LegacyRecorder()
    async with db.get_sessionmaker()() as s:
        assert await notifications.dispatch_due(s, rec, now=T0) == ["notification.sent"]
    assert rec.calls and rec.calls[0][0] == "oncall.page"


async def test_webhook_sends_idempotency_key_header(session):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    notifier = notifications.WebhookNotifier(
        "https://oncall.example/hook", transport=httpx.MockTransport(handler)
    )
    row = notifications.stage(session, _item(), now=T0)
    await session.commit()
    async with db.get_sessionmaker()() as s:
        assert await notifications.dispatch_due(s, notifier, now=T0) == ["notification.sent"]
    assert seen[0].headers["Idempotency-Key"] == row.idempotency_key
    assert b'"idempotency_key"' in seen[0].content


async def test_dedup_key_exists(session):
    notifications.stage(session, _item(), idempotency_key="sla:i-1:breached", now=T0)
    await session.commit()
    assert await notifications.exists(session, "sla:i-1:breached")
    assert not await notifications.exists(session, "sla:i-1:at_risk")
