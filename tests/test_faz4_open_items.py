"""Faz 4 açık konular: kuyruk saklama süresi, Slack nöbetçi tekilliği, SMTP sertleştirme, olay
ajanının indeksli sorguları, MTTA'da iptal hariç, CSI geçmiş toplamları SQL'de, yapay zekâ / MCP
yazmalarında zorunlu sürüm (If-Match / expected_version).

Ağ yok: Slack `httpx.MockTransport`, SMTP sahte `smtplib` sınıfları.
"""

from __future__ import annotations

import json
import smtplib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
import pytest
from _auth import AGENT as AGENT_H
from _events import EV_SETTINGS, T0, am_alert, am_payload, chain, ci
from _tooling import AGENT, AI, LIVE, seed
from sqlalchemy import select, text, update

from serviszeka import audit, auth, db, notifications, scheduler
from serviszeka.agents import events as event_agent
from serviszeka.agents import improvement
from serviszeka.config import Settings
from serviszeka.connectors import ConnectorError
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.setup import ConnectorConfigError, check_startup
from serviszeka.connectors.slack import SlackClient, SlackOncallNotifier, SlackWebhook
from serviszeka.connectors.smtp import SmtpNotifier, build_smtp
from serviszeka.domain.enums import (
    ActionRequestState,
    CIClass,
    IncidentStatus,
    NotificationState,
    Priority,
    TicketKind,
)
from serviszeka.main import app
from serviszeka.models import ActionRequest, AuditLog, Incident, NotificationOutbox
from serviszeka.services import events, kpi
from serviszeka.tools import ToolStatus, ToolValidationError, execute_tool

DAY = timedelta(days=1)


# =====================================================================================================
# 1) Kuyruk saklama süresi (outbox retention)
# =====================================================================================================


def _item(entity_id: str = "i-1") -> notifications.PendingNotification:
    return notifications.PendingNotification(
        event="oncall.page",
        payload={"message": "deneme"},
        actor="ayse",
        entity_type="incident",
        entity_id=entity_id,
    )


async def _incident(session, status: IncidentStatus, n: int) -> Incident:
    inc = Incident(
        number=f"INC9{n:06d}",
        kind=TicketKind.INCIDENT,
        title="SLA",
        caller="ali",
        priority=Priority.P1,
        status=status,
        created_at=T0 - 200 * DAY,
        updated_at=T0 - 200 * DAY,
    )
    session.add(inc)
    await session.flush()
    return inc


async def _row(session, key: str, state: NotificationState, age: timedelta, entity: str = "i-1"):
    row = notifications.stage(session, _item(entity), idempotency_key=key, now=T0 - age)
    await session.flush()
    values = {"state": state, "updated_at": T0 - age}
    if state == NotificationState.SENT:
        values["sent_at"] = T0 - age
    await session.execute(
        update(NotificationOutbox).where(NotificationOutbox.id == row.id).values(**values)
    )
    return row


async def _keys() -> set[str]:
    async with db.get_sessionmaker()() as s:
        return set((await s.scalars(select(NotificationOutbox.idempotency_key))).all())


async def test_retention_deletes_old_sent_and_dead_rows_but_keeps_open_sla_dedup(session):
    open_inc = await _incident(session, IncidentStatus.IN_PROGRESS, 1)
    resolved = await _incident(session, IncidentStatus.RESOLVED, 2)
    closed = await _incident(session, IncidentStatus.CLOSED, 3)
    S, D, F, P = (NotificationState.SENT, NotificationState.DEAD, NotificationState.FAILED,
                  NotificationState.PENDING)  # fmt: skip
    for i in range(5):  # toplu silme (batch 2) birden çok turda
        await _row(session, f"old-sent-{i}", S, 31 * DAY)
    await _row(session, "new-sent", S, 29 * DAY)
    await _row(session, "old-dead", D, 91 * DAY)
    await _row(session, "young-dead", D, 60 * DAY)
    await _row(session, "old-failed", F, 200 * DAY)
    await _row(session, "old-pending", P, 200 * DAY)
    # SLA tekilleştirme satırları: olay açıkken silinmez (silinirse aynı ihlal yeniden çağrılır)
    await _row(session, f"sla:{open_inc.id}:breached", S, 120 * DAY, open_inc.id)
    await _row(session, f"sla:{open_inc.id}:at_risk", D, 120 * DAY, open_inc.id)
    await _row(session, f"sla:{resolved.id}:breached", S, 120 * DAY, resolved.id)
    await _row(session, f"sla:{closed.id}:breached", S, 120 * DAY, closed.id)
    await _row(session, "sla:yok-olay:breached", S, 120 * DAY, "yok-olay")
    await session.commit()

    settings = Settings(outbox_retention_days=30, outbox_dead_retention_days=90)
    async with db.get_sessionmaker()() as s:
        result = await notifications.purge_expired(s, now=T0, settings=settings, batch_size=2)
    assert result == {"sent": 8, "dead": 1, "batches": 5}
    assert await _keys() == {
        "new-sent", "young-dead", "old-failed", "old-pending",
        f"sla:{open_inc.id}:breached", f"sla:{open_inc.id}:at_risk",
    }  # fmt: skip
    # İkinci çalışma bir şey silmez
    async with db.get_sessionmaker()() as s:
        again = await notifications.purge_expired(s, now=T0, settings=settings, batch_size=2)
    assert again == {"sent": 0, "dead": 0, "batches": 0}


async def test_retention_is_a_scheduler_job_and_keeps_sla_dedup_effective(session):
    names = {j.name: j for j in scheduler.DEFAULT_JOBS}
    assert "outbox_retention" in names and names["outbox_retention"].interval >= 3600
    assert Settings().outbox_retention_days == 30 and Settings().outbox_dead_retention_days == 90

    open_inc = await _incident(session, IncidentStatus.IN_PROGRESS, 4)
    key = scheduler.sla_dedup_key(open_inc.id, "breached")
    await _row(session, key, NotificationState.SENT, 400 * DAY, open_inc.id)
    await _row(session, "eski", NotificationState.SENT, 400 * DAY)
    await session.commit()

    class Rec:
        async def notify(self, event, payload, *, idempotency_key=None):
            pass

    sched = scheduler.Scheduler(
        Settings(), clock=lambda: T0, notifier_factory=lambda s: Rec(), owner="k1"
    )
    run = await sched.run_job("outbox_retention")
    assert run.status == "ok" and run.result["sent"] == 1
    assert await _keys() == {key}
    async with db.get_sessionmaker()() as s:
        assert await notifications.exists(s, key)  # SLA uyarısı yeniden gönderilmez


# =====================================================================================================
# 2) Nöbetçi tekilliği: "sending" kirası + Slack geçmiş denetimi
# =====================================================================================================


class KwRecorder:
    def __init__(self, result=None, fail: int = 0):
        self.calls: list[dict] = []
        self.result = result
        self.fail = fail

    async def notify(self, event, payload, *, idempotency_key=None, possibly_delivered=False):
        self.calls.append({"key": idempotency_key, "possibly_delivered": possibly_delivered})
        if self.fail:
            self.fail -= 1
            raise TimeoutError("zaman aşımı")
        return self.result


async def _staged(session) -> NotificationOutbox:
    row = notifications.stage(session, _item(), idempotency_key="ntf:sabit", now=T0)
    await session.commit()
    return row


async def test_claim_marks_row_sending_and_recovery_is_flagged(session):
    row = await _staged(session)
    # Gönderici kirayı aldı, gönderdi ve sonucu yazamadan çöktü
    async with db.get_sessionmaker()() as s:
        assert await notifications._claim(s, row.id, T0) is True
    async with db.get_sessionmaker()() as s:
        saved = await s.get(NotificationOutbox, row.id)
        assert saved.state == NotificationState.SENDING and saved.attempts == 1
        rec = KwRecorder()
        # Kira dolmadan kimse göndermez
        assert await notifications.dispatch_due(s, rec, now=T0 + timedelta(seconds=60)) == []
        later = T0 + timedelta(seconds=notifications.CLAIM_SECONDS)
        assert await notifications.dispatch_due(s, rec, now=later) == ["notification.sent"]
    assert rec.calls == [{"key": "ntf:sabit", "possibly_delivered": True}]


async def test_first_attempt_is_not_flagged_but_retry_after_failure_is(session):
    row = await _staged(session)
    rec = KwRecorder(fail=1)
    async with db.get_sessionmaker()() as s:
        assert await notifications.dispatch_due(s, rec, now=T0) == ["notification.failed"]
        later = T0 + timedelta(hours=2)
        assert await notifications.dispatch_due(s, rec, now=later) == ["notification.sent"]
    # Zaman aşımı belirsizdir (alıcı almış olabilir): yeniden denemede alıcı yinelemeyi denetler
    assert [c["possibly_delivered"] for c in rec.calls] == [False, True]
    async with db.get_sessionmaker()() as s:
        assert (await s.get(NotificationOutbox, row.id)).state == NotificationState.SENT


async def test_receiver_reported_duplicate_is_recorded_as_sent(session):
    row = await _staged(session)
    rec = KwRecorder(result={"deduplicated": True})
    async with db.get_sessionmaker()() as s:
        assert await notifications.dispatch_due(s, rec, now=T0) == ["notification.sent"]
        log = await s.scalar(select(AuditLog).where(AuditLog.action == "notification.sent"))
        assert log.details["deduplicated"] is True
        assert (await s.get(NotificationOutbox, row.id)).state == NotificationState.SENT


TOKEN = "xoxb-gizli-belirtec-123"
HOOK = "https://hooks.slack.com/services/T000/B000/gizli"


class SlackFake:
    def __init__(self, history: object = None) -> None:
        self.history = history if history is not None else {"ok": True, "messages": []}
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if request.url.host != "slack.com":
            return httpx.Response(200, text="ok")
        if method == "conversations.history":
            return httpx.Response(200, json=self.history)
        if method == "chat.postMessage":
            return httpx.Response(200, json={"ok": True, "ts": "1.2", "channel": "C0ONCALL"})
        return httpx.Response(404)

    def methods(self) -> list[str]:
        return [c.url.path.rsplit("/", 1)[-1] for c in self.calls]


def _oncall(fake: SlackFake) -> SlackOncallNotifier:
    return SlackOncallNotifier(
        SlackClient(TOKEN, transport=httpx.MockTransport(fake)), channel="C0ONCALL"
    )


def _posted(key: str) -> dict:
    return {
        "ts": "1.1",
        "text": "Nöbetçi uyarısı",
        "metadata": {"event_type": "serviszeka_oncall", "event_payload": {"idempotency_key": key}},
    }


async def test_slack_oncall_message_carries_idempotency_metadata():
    fake = SlackFake()
    await _oncall(fake).notify("sla.breached", {"message": "INC1 ihlal"}, idempotency_key="sla:1:b")
    assert fake.methods() == ["chat.postMessage"]  # ilk denemede geçmiş okunmaz
    body = json.loads(fake.calls[0].content)
    assert body["metadata"] == {
        "event_type": "serviszeka_oncall",
        "event_payload": {"idempotency_key": "sla:1:b"},
    }


async def test_slack_oncall_retry_skips_when_history_has_key():
    fake = SlackFake({"ok": True, "messages": [{"ts": "1.0", "text": "x"}, _posted("sla:1:b")]})
    result = await _oncall(fake).notify(
        "sla.breached", {"message": "x"}, idempotency_key="sla:1:b", possibly_delivered=True
    )
    assert result == {"deduplicated": True}
    assert fake.methods() == ["conversations.history"]
    params = fake.calls[0].url.params
    assert params["channel"] == "C0ONCALL" and params["include_all_metadata"] == "true"
    assert float(params["oldest"]) > 0


async def test_slack_oncall_retry_posts_when_key_absent_or_history_unreadable():
    fake = SlackFake({"ok": True, "messages": [_posted("baska-anahtar")]})
    await _oncall(fake).notify("e", {"message": "x"}, idempotency_key="k", possibly_delivered=True)
    assert fake.methods() == ["conversations.history", "chat.postMessage"]
    # Geçmiş okunamıyorsa (kapsam yok) çift uyarı, kaçırılan uyarıya tercih edilir
    fake = SlackFake({"ok": False, "error": "missing_scope", "needed": "channels:history"})
    await _oncall(fake).notify("e", {"message": "x"}, idempotency_key="k", possibly_delivered=True)
    assert fake.methods() == ["conversations.history", "chat.postMessage"]


async def test_slack_webhook_oncall_cannot_check_history_and_shows_reference():
    fake = SlackFake()
    hook = SlackOncallNotifier(webhook=SlackWebhook(HOOK, transport=httpx.MockTransport(fake)))
    await hook.notify("e", {"message": "x"}, idempotency_key="sla:9:b", possibly_delivered=True)
    assert len(fake.calls) == 1 and "sla:9:b" in fake.calls[0].content.decode()


async def test_slack_oncall_dedup_end_to_end_through_outbox(session):
    row = await _staged(session)
    async with db.get_sessionmaker()() as s:
        assert await notifications._claim(s, row.id, T0) is True  # gönderdi, çöktü
    fake = SlackFake({"ok": True, "messages": [_posted("ntf:sabit")]})
    later = T0 + timedelta(seconds=notifications.CLAIM_SECONDS)
    async with db.get_sessionmaker()() as s:
        assert await notifications.dispatch_due(s, _oncall(fake), now=later) == [
            "notification.sent"
        ]
    assert fake.methods() == ["conversations.history"]  # ikinci kez çağrılmadı


# =====================================================================================================
# 3) SMTP: TLS zorunlu, başlık enjeksiyonu
# =====================================================================================================


def _directory() -> InMemoryDirectory:
    d = InMemoryDirectory()
    d.add_user("ali", "Ali Demir", other_mails=["ali@kisisel.ornek"])
    d.add_user("kotu", "Kötü", other_mails=["x@ornek.com\r\nBcc: kurban@ornek.com"])
    return d


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []
    supports_starttls = True

    def __init__(self, host, port, timeout=None, context=None):
        self.ops: list[str] = ["ssl" if context is not None else "plain"]
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        if not self.supports_starttls:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        self.ops.append("starttls")

    def login(self, user, password):
        self.ops.append("login")

    def send_message(self, message):
        self.ops.append("send")


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.supports_starttls = True
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def _smtp(**kw) -> SmtpNotifier:
    return SmtpNotifier(_directory(), host="smtp.ornek.com", sender="sm@ornek.com", **kw)


async def test_smtp_plaintext_refused_unless_explicitly_allowed(fake_smtp):
    with pytest.raises(ConnectorError, match="TLS"):
        _smtp(starttls=False)
    n = _smtp(starttls=False, allow_plaintext=True)
    await n.send_to_user("ali", "Konu", "Gövde")
    assert fake_smtp.instances[-1].ops == ["plain", "send"]


async def test_smtp_starttls_before_login_and_no_downgrade(fake_smtp):
    await _smtp(username="u", password="p").send_to_user("ali", "Konu", "Gövde")
    assert fake_smtp.instances[-1].ops == ["plain", "starttls", "login", "send"]
    # Sunucu STARTTLS sunmuyorsa düz metne düşülmez, kimlik bilgisi gönderilmez
    fake_smtp.supports_starttls = False
    with pytest.raises(ConnectorError) as err:
        await _smtp(username="u", password="p").send_to_user("ali", "Konu", "Gövde")
    assert "login" not in fake_smtp.instances[-1].ops and "STARTTLS extension" not in str(err.value)


async def test_smtp_implicit_tls(fake_smtp):
    await _smtp(starttls=False, ssl=True, port=465).send_to_user("ali", "Konu", "Gövde")
    assert fake_smtp.instances[-1].ops == ["ssl", "send"]
    with pytest.raises(ConnectorError):
        _smtp(starttls=True, ssl=True)  # ikisi birden geçersiz


async def test_smtp_header_injection_is_connector_error_without_content():
    sent: list = []
    n = SmtpNotifier(_directory(), host="h", sender="sm@ornek.com", send=sent.append)
    with pytest.raises(ConnectorError) as err:
        await n.send_to_user("ali", "Merhaba\r\nBcc: kurban@ornek.com", "Gövde")
    assert "kurban" not in str(err.value) and "Merhaba" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    with pytest.raises(ConnectorError) as err:
        await n.send_to_user("kotu", "Geçici parola", "Parola: X1", sensitive=True)
    assert "kurban" not in str(err.value) and "X1" not in str(err.value)
    assert sent == []


SMTP_ON = {"smtp_host": "smtp.ornek.com", "smtp_sender": "sm@ornek.com"}


def test_smtp_startup_checks():
    check_startup(Settings(**SMTP_ON))  # STARTTLS varsayılan
    check_startup(Settings(**SMTP_ON, smtp_starttls=False, smtp_ssl=True))
    with pytest.raises(ConnectorConfigError, match="SZ_SMTP_ALLOW_PLAINTEXT"):
        check_startup(Settings(**SMTP_ON, smtp_starttls=False))
    with pytest.raises(ConnectorConfigError, match="development"):
        check_startup(
            Settings(**SMTP_ON, smtp_starttls=False, smtp_allow_plaintext=True,
                     environment="production")
        )  # fmt: skip
    check_startup(
        Settings(**SMTP_ON, smtp_starttls=False, smtp_allow_plaintext=True,
                 environment="development")
    )  # fmt: skip
    with pytest.raises(ConnectorConfigError):
        check_startup(Settings(**SMTP_ON, smtp_ssl=True))  # STARTTLS + örtük TLS birlikte
    # Kurulum da geliştirme dışında düz metne izin vermez (savunma derinliği)
    with pytest.raises(ConnectorError):
        build_smtp(
            Settings(**SMTP_ON, smtp_starttls=False, smtp_allow_plaintext=True,
                     environment="production"), None,
        )  # fmt: skip


# =====================================================================================================
# 4) Olay ajanı: bekleyen istek sorguları indeksli SQL
# =====================================================================================================


async def _ingest(session, *alerts, at=T0):
    res = await events.ingest(
        session, events.parse_alertmanager(am_payload(*alerts)),
        settings=EV_SETTINGS, now=at,
    )  # fmt: skip
    await session.flush()
    return res


EV_LIVE = EV_SETTINGS.model_copy(update={"ai_shadow_mode": False, "ai_confidence_threshold": 0.5})


async def test_create_request_records_indexed_group_column(session):
    await chain(session)
    res = await _ingest(session, am_alert("fp-host", ci="db-host-01"))
    [out] = await event_agent.process_groups(session, res.group_ids, settings=EV_LIVE, now=T0)
    assert out.status == "pending_approval"
    ar = await session.get(ActionRequest, out.action_request_id)
    assert ar.event_group_id == res.group_ids[0]


async def test_blocked_and_pending_lookups_do_not_load_other_groups(session):
    await chain(session)
    res = await _ingest(session, am_alert("fp-host", ci="db-host-01"))
    group_id = res.group_ids[0]
    [out] = await event_agent.process_groups(session, [group_id], settings=EV_LIVE, now=T0)
    # Başka gruplar için çok sayıda bekleyen istek (eski yol hepsini Python'da tarardı)
    for i in range(30):
        session.add(
            ActionRequest(
                number=f"ACT9{i:06d}", tool="event.create_incident",
                args={"group_id": f"baska-{i}"}, args_display={}, args_hash="x",
                risk_class="R2", state=ActionRequestState.PENDING, requested_by="ai:events",
                event_group_id=f"baska-{i}", expires_at=T0 + DAY,
            )
        )  # fmt: skip
    await session.commit()
    async with db.get_sessionmaker()() as s:
        group = await s.get(events.EventGroup, group_id)
        assert (await event_agent._create_blocked(s, group)).startswith("bekleyen istek")
        assert await events.pending_create_request(s, group_id) == out.action_request_id
        loaded = [o for o in s.identity_map.values() if isinstance(o, ActionRequest)]
        # Yalnızca bu grubun isteği okunur; başka grupların satırları yüklenmez
        assert {o.id for o in loaded} <= {out.action_request_id}


async def test_event_request_lookups_use_index(session):
    conn = await session.connection()
    plan = await conn.execute(
        text(
            "EXPLAIN QUERY PLAN SELECT id FROM action_requests "
            "WHERE event_group_id = :g AND tool = 'event.create_incident'"
        ),
        {"g": "x"},
    )
    detail = " ".join(str(r[-1]) for r in plan.all())
    assert "ix_action_requests_event_group_id" in detail, detail


# =====================================================================================================
# 5) MTTA: iptal edilen olaylar hariç (MTTR ile tutarlı)
# =====================================================================================================


async def test_mtta_excludes_cancelled_incidents(session):
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    base = now - 5 * DAY
    for n, (hours, status) in enumerate(
        [(1, IncidentStatus.IN_PROGRESS), (3, IncidentStatus.RESOLVED), (50, IncidentStatus.CANCELLED)]
    ):
        session.add(
            Incident(
                number=f"INC8{n:06d}", kind=TicketKind.INCIDENT, title="t", caller="ali",
                priority=Priority.P3, status=status, assignment_group="network_ops",
                created_at=base, updated_at=base, responded_at=base + timedelta(hours=hours),
            )
        )  # fmt: skip
    await session.flush()
    report = await kpi.report(session, end=now, days=30, keys=["mtta"], now=now)
    [mtta] = report.kpis
    assert mtta.count == 2 and mtta.value == pytest.approx(2.0)
    definition = kpi.DEFINITIONS["mtta"]
    assert "iptal hariç" in definition.formula


# =====================================================================================================
# 6) CSI: denetim geçmişi toplamları SQL'de (Python yorumuyla aynı sonuç)
# =====================================================================================================


DETAILS = [
    {"changes": {"assignment_group": ["service_desk", "network_ops"]}},  # yeniden atama
    {"changes": {"assignment_group": [None, "network_ops"]}},  # ilk atama
    {"changes": {"assignment_group": ["", "network_ops"]}},  # ilk atama
    {"changes": {"assignment_group": ["network_ops", "network_ops"]}},  # değişiklik yok
    {"changes": {"assignment_group": ["network_ops", None]}},  # atama kaldırıldı
    {"changes": {"status": ["resolved", "in_progress"]}},  # yeniden açma
    {"changes": {"status": ["resolved", "closed"]}},  # kapanış
    {"changes": {"status": ["resolved", "resolved"]}},
    {"changes": {"status": ["in_progress", "resolved"]}},  # çözüm
    {"changes": {"status": "resolved"}},  # biçimsiz
    {"changes": "x"},
    {"other": 1},
    {},
]


async def test_first_contact_sql_predicates_match_python(session):
    ids = []
    for i, details in enumerate(DETAILS):
        entity = f"inc-{i}"
        ids.append(entity)
        await audit.record(
            session, actor="ayse", action="incident.update", entity_type="incident",
            entity_id=entity, details=details,
        )  # fmt: skip
    # Başka eylem / tür satırları sayılmaz
    await audit.record(
        session, actor="ayse", action="incident.work_note", entity_type="incident",
        entity_id="inc-0b", details=DETAILS[0],
    )  # fmt: skip
    await session.flush()
    expected = {
        f"inc-{i}" for i, d in enumerate(DETAILS) if kpi.is_reassignment(d) or kpi.is_reopen(d)
    }
    assert expected == {"inc-0", "inc-4", "inc-5"}
    assert await kpi.not_first_contact_ids(session, [*ids, "inc-0b"]) == expected


async def _kb_case(session, article_number: str, statuses, *, touch=None) -> str:
    from serviszeka.domain.enums import DecisionOutcome
    from serviszeka.models import AIDecision, KnowledgeArticle

    article = KnowledgeArticle(number=article_number, title="VPN", body="x", state="published")
    session.add(article)
    await session.flush()
    for n, status in enumerate(statuses):
        inc = Incident(
            number=f"INC7{article_number[-2:]}{n:04d}", kind=TicketKind.INCIDENT, title="t",
            caller="ali", priority=Priority.P4, status=status, created_at=T0 - 5 * DAY,
            updated_at=T0 - 5 * DAY,
            resolved_at=T0 - 4 * DAY if status != IncidentStatus.IN_PROGRESS else None,
        )  # fmt: skip
        session.add(inc)
        await session.flush()
        session.add(
            AIDecision(
                incident_id=inc.id, agent="service_desk", action="classify", input_summary="",
                sources=[article.id], model="m", confidence=0.9,
                outcome=DecisionOutcome.AUTO_APPLIED, created_at=T0 - 5 * DAY,
            )
        )  # fmt: skip
        if touch and n in touch:
            await audit.record(
                session, actor="ayse", action="incident.update", entity_type="incident",
                entity_id=inc.id, details=touch[n],
            )  # fmt: skip
    await session.flush()
    return article.id


async def test_kb_signal_counts_first_contact_in_sql(session):
    R, X, O = IncidentStatus.RESOLVED, IncidentStatus.CANCELLED, IncidentStatus.IN_PROGRESS
    reassigned = {"changes": {"assignment_group": ["service_desk", "network_ops"]}}
    reopened = {"changes": {"status": ["resolved", "in_progress"]}}
    first_assign = {"changes": {"assignment_group": [None, "network_ops"]}}
    # Hiçbiri ilk temasta çözülmedi (yeniden atama/açma, iptal, açık) -> sinyal
    bad = await _kb_case(session, "KB0000091", [R, R, X, O, O],
                         touch={0: reassigned, 1: reopened})  # fmt: skip
    # Biri ilk temasta çözüldü (ilk atama yeniden atama sayılmaz) -> sinyal yok
    good = await _kb_case(session, "KB0000092", [R, R, O, O, O],
                          touch={0: first_assign, 1: reassigned})  # fmt: skip
    signals = await improvement._kb_signals(session, kpi.Window(T0 - 30 * DAY, T0))
    assert [s.key for s in signals] == [f"kb_unhelpful:{bad}"]
    assert signals[0].evidence["suggested"] == 5 and signals[0].evidence["resolved"] == 3
    assert good not in signals[0].key


# =====================================================================================================
# 7) Yapay zekâ / MCP yazmalarında zorunlu sürüm
# =====================================================================================================


async def test_ai_incident_tool_requires_expected_version(session):
    fx = await seed(session)
    args = {"incident_id": fx.incident_id, "assignment_group": "network_ops"}
    with pytest.raises(ToolValidationError, match="expected_version"):
        await execute_tool(session, AI, "incident.assign", args, confidence=1.0, settings=LIVE)
    with pytest.raises(ToolValidationError):
        await execute_tool(
            session, AI, "incident.resolve", {"incident_id": fx.incident_id, "resolution": "x"},
            settings=LIVE,
        )  # fmt: skip
    assert (await session.scalars(select(ActionRequest))).all() == []
    inc = await session.get(Incident, fx.incident_id)
    r = await execute_tool(
        session, AI, "incident.assign", {**args, "expected_version": inc.version},
        confidence=1.0, settings=LIVE,
    )  # fmt: skip
    assert r.status == ToolStatus.EXECUTED
    # İnsan için geriye uyumlu (sürüm isteğe bağlı) ...
    r = await execute_tool(session, AGENT, "incident.assign", {**args, "assignee": "mehmet"})
    assert r.status == ToolStatus.EXECUTED
    # ... ancak MCP üzerinden insan çağrısında da zorunlu
    with pytest.raises(ToolValidationError):
        await execute_tool(session, AGENT, "incident.assign", args, require_version=True)
    # Yaratma / okuma araçlarında sürüm yok: etkilenmez
    r = await execute_tool(session, AI, "incident.get", {"incident_id": fx.incident_id})
    assert r.status == ToolStatus.EXECUTED


async def test_events_agent_resolve_proposal_carries_version(session):
    await ci(session, "rapor-sunucu", CIClass.SERVER, 4)
    res = await _ingest(session, am_alert("fp-r1", severity="error", ci="rapor-sunucu"))
    [out] = await event_agent.process_groups(session, res.group_ids, settings=EV_LIVE, now=T0)
    assert out.status == "executed"
    later = T0 + timedelta(minutes=5)
    res2 = await _ingest(
        session, am_alert("fp-r1", status="resolved", severity="error", ci="rapor-sunucu",
                          ends=later), at=later,
    )  # fmt: skip
    outs = await event_agent.process_groups(session, res2.group_ids, settings=EV_LIVE, now=later)
    assert outs[0].action == "propose_resolve" and outs[0].status == "pending_approval"
    ar = await session.get(ActionRequest, outs[0].action_request_id)
    inc = await session.get(Incident, out.incident_id)
    assert ar.args["expected_version"] == inc.version


@pytest.fixture
async def api(session) -> AsyncIterator[tuple[httpx.AsyncClient, dict]]:
    who = {"user": auth.CurrentUser("ai:copilot", frozenset({"agent"}))}

    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, who
    app.dependency_overrides.clear()


async def test_patch_requires_if_match_for_ai_actor_only(api, session):
    client, who = api
    fx = await seed(session)
    # İnsan (portal) için geriye uyumlu: If-Match'siz PATCH çalışır
    r = await client.patch(f"/incidents/{fx.incident_id}", json={"assignee": "mehmet"},
                           headers=AGENT_H)  # fmt: skip
    assert r.status_code == 200, r.text
    version = r.json()["version"]

    app.dependency_overrides[auth.get_current_user] = lambda: who["user"]
    r = await client.patch(f"/incidents/{fx.incident_id}", json={"assignee": "ali"})
    assert r.status_code == 428 and "If-Match" in r.json()["detail"]
    r = await client.put(f"/incidents/{fx.incident_id}/cis", json={"ci_ids": [fx.ci_id]})
    assert r.status_code == 428
    r = await client.patch(f"/changes/{fx.change_id}", json={"title": "Yeni"})
    assert r.status_code == 428
    r = await client.patch(
        f"/incidents/{fx.incident_id}", json={"assignee": "ali"},
        headers={"If-Match": f'"{version}"'},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    r = await client.patch(
        f"/incidents/{fx.incident_id}", json={"assignee": "veli", "expected_version": version + 1}
    )
    assert r.status_code == 200, r.text


def test_migration_0014_adds_event_group_column_backfills_and_downgrades(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    root = Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m14.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_file}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0013")
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        with engine.begin() as conn:
            for rid, tool, args in (
                ("a1", "event.create_incident", '{"group_id": "g-1"}'),
                ("a2", "event.silence", '{"group_id": "g-2", "minutes": 60}'),
                ("a3", "incident.resolve", '{"incident_id": "i-1"}'),
            ):
                conn.exec_driver_sql(
                    "INSERT INTO action_requests (id, number, tool, args, args_display, args_hash, "
                    "risk_class, state, required_approvals, requested_by, reason, version, "
                    "expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, '{}', 'h', 'R2', "
                    "'PENDING', 1, 'ai:events', '', 1, '2026-01-02 00:00:00', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00')",
                    (rid, f"ACT{rid}", tool, args),
                )
        command.upgrade(cfg, "head")
        insp = inspect(engine)
        assert "ix_action_requests_event_group_id" in {
            i["name"] for i in insp.get_indexes("action_requests")
        }
        with engine.connect() as conn:
            rows = {r[0]: r[1] for r in conn.exec_driver_sql("SELECT id, event_group_id FROM action_requests")}
        assert rows == {"a1": "g-1", "a2": "g-2", "a3": None}
        assert "ix_audit_log_entity" in {i["name"] for i in insp.get_indexes("audit_log")}
        command.downgrade(cfg, "0013")
        cols = {c["name"] for c in inspect(engine).get_columns("action_requests")}
        assert "event_group_id" not in cols
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()
