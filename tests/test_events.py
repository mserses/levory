"""Faz 4 — izleme ve olay yönetimi: webhook doğrulama, ayrıştırma, CI eşleme, bağıntı, olay açma.

Ağ yok: webhook'lar ASGI üzerinden, CMDB bellek içi SQLite. Kapı davranışları (P1 onaysız açılmaz,
çözülen uyarı olayı çözmez) ayrıca `tests/test_gate_no_unapproved_risk.py` bölüm 8'de.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _auth import ADMIN, AGENT, APPROVER, dev_headers
from _events import (
    EV_SETTINGS,
    SECRET,
    T0,
    am_alert,
    am_payload,
    body_of,
    chain,
    ci,
    signed_headers,
    token_headers,
)
from sqlalchemy import func, select

from serviszeka import db
from serviszeka.agents import events as event_agent
from serviszeka.api import events as events_api
from serviszeka.auth import CurrentUser
from serviszeka.config import EventCIRule, Settings
from serviszeka.domain.enums import CIClass, EventGroupState, EventSeverity, EventStatus
from serviszeka.main import app
from serviszeka.models import (
    ActionRequest,
    AuditLog,
    Event,
    EventDelivery,
    EventGroup,
    Incident,
)
from serviszeka.services import cmdb, events
from serviszeka.services import incidents as incident_service
from serviszeka.services.cmdb_schemas import CIUpdate
from serviszeka.tools import actions

LIVE = EV_SETTINGS.model_copy(update={"ai_shadow_mode": False, "ai_confidence_threshold": 0.0})
HUMAN = CurrentUser("sef.ali", frozenset({"approver"}))


def _now() -> datetime:
    return datetime.now(UTC)


async def _ingest(session, *alerts, at: datetime, settings: Settings = LIVE):
    res = await events.ingest(
        session, events.parse_alertmanager(am_payload(*alerts)), settings=settings, now=at
    )
    await session.flush()
    return res


async def _count(session, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for w in where:
        stmt = stmt.where(w)
    return int(await session.scalar(stmt) or 0)


# --- 1) webhook doğrulama ----------------------------------------------------------------------


@pytest.fixture
async def client(session) -> AsyncIterator[httpx.AsyncClient]:
    state = {"settings": LIVE}

    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[events_api.get_event_settings] = lambda: state["settings"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        c.state = state  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _payload(fp: str = "fp-1", **labels: str) -> dict:
    now = _now().replace(microsecond=0)
    return am_payload(am_alert(fp, starts=now - timedelta(minutes=1), **labels))


async def test_webhook_rejected_when_secret_unset(client, session):
    client.state["settings"] = LIVE.model_copy(update={"events_webhook_secret": None})
    body = body_of(_payload())
    for headers in (signed_headers(body), token_headers()):
        r = await client.post("/events/alertmanager", content=body, headers=headers)
        assert r.status_code == 401, r.text
    assert await _count(session, Event) == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json"},  # kimlik bilgisi yok
        token_headers("yanlis-gizli"),
        {"X-ServisZeka-Token": "yanlis", "Content-Type": "application/json"},
        {
            "Authorization": "Bearer dev:ayse:agent",
            "Content-Type": "application/json",
        },  # OIDC değil
    ],
)
async def test_webhook_bad_secret_401(client, session, headers):
    r = await client.post("/events/alertmanager", content=body_of(_payload()), headers=headers)
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers
    assert await _count(session, Event) == 0
    assert await _count(session, EventDelivery) == 0


async def test_webhook_bad_signature_and_stale_timestamp_401(client, session):
    body = body_of(_payload())
    bad = signed_headers(body, secret="baska-gizli")
    r = await client.post("/events/alertmanager", content=body, headers=bad)
    assert r.status_code == 401 and "İmza" in r.json()["detail"]
    # Gövde değiştirilmiş (imza eski gövdenin)
    tampered = signed_headers(body)
    r = await client.post("/events/alertmanager", content=body + b" ", headers=tampered)
    assert r.status_code == 401
    # Pencere dışı zaman damgası (doğru imzayla bile)
    old = int(_now().timestamp()) - LIVE.events_replay_window_seconds - 5
    r = await client.post(
        "/events/alertmanager", content=body, headers=signed_headers(body, ts=old)
    )
    assert r.status_code == 401 and "pencere" in r.json()["detail"]
    # İmza var, zaman damgası yok
    no_ts = {k: v for k, v in signed_headers(body).items() if k != "X-ServisZeka-Timestamp"}
    r = await client.post("/events/alertmanager", content=body, headers=no_ts)
    assert r.status_code == 401
    assert await _count(session, Event) == 0


async def test_webhook_hmac_ok_and_replay_is_duplicate(client, session):
    body = body_of(_payload("fp-replay", ci="yok"))
    headers = signed_headers(body)
    r = await client.post("/events/alertmanager", content=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1 and r.json()["duplicate"] is False
    # Aynı teslimat (imza + zaman damgası + gövde) yeniden oynatılıyor: hiçbir şey yazılmaz
    audits = await _count(session, AuditLog)
    r = await client.post("/events/alertmanager", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert await _count(session, Event) == 1
    assert (await session.scalar(select(Event))).received_count == 1
    assert await _count(session, AuditLog) == audits


async def test_webhook_shared_secret_ok_and_dedup(client, session):
    body = body_of(_payload("fp-token"))
    r = await client.post("/events/alertmanager", content=body, headers=token_headers())
    assert r.status_code == 200 and r.json()["created"] == 1
    r = await client.post("/events/alertmanager", content=body, headers=token_headers())
    assert r.json()["duplicate"] is True
    # X-ServisZeka-Token başlığı da kabul edilir; farklı gövde yeni teslimattır
    body2 = body_of(_payload("fp-token-2"))
    r = await client.post(
        "/events/alertmanager",
        content=body2,
        headers={"X-ServisZeka-Token": SECRET, "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json()["created"] == 1


async def test_webhook_body_limit_and_bad_payload(client, session):
    client.state["settings"] = LIVE.model_copy(update={"events_max_body_bytes": 1024})
    big = body_of(_payload("fp-big", note="x" * 2000))
    r = await client.post("/events/alertmanager", content=big, headers=token_headers())
    assert r.status_code == 413
    r = await client.post("/events/alertmanager", content=b"{oops", headers=token_headers())
    assert r.status_code == 422
    wrong_version = {**_payload(), "version": "3"}
    r = await client.post(
        "/events/alertmanager", content=body_of(wrong_version), headers=token_headers()
    )
    assert r.status_code == 422 and "version" in json.dumps(r.json())
    r = await client.post(
        "/events/generic", content=body_of({"events": [{"bilinmeyen": 1}]}), headers=token_headers()
    )
    assert r.status_code == 422
    assert await _count(session, Event) == 0


# --- 2) ayrıştırma + maskeleme -----------------------------------------------------------------


def test_parse_alertmanager_v4():
    alert = am_alert("abc", ends=None, instance="db-host-01:9100", password="gizli123")
    alert["annotations"]["description"] = "Sahibi ali.veli@ornek.com aradı"
    [a] = events.parse_alertmanager(am_payload(alert))
    assert a.source == "alertmanager" and a.fingerprint == "abc"
    assert a.status == EventStatus.FIRING and a.severity == EventSeverity.CRITICAL
    assert a.starts_at == T0.replace(microsecond=123456)  # nanosaniye kesri mikrosaniyeye
    assert a.ends_at is None  # sıfır endsAt
    assert a.labels["password"] == "***" and a.labels["instance"] == "db-host-01:9100"
    assert "ali.veli@ornek.com" not in a.annotations["description"]
    assert a.title == "HighErrorRate: HighErrorRate uyarısı"
    # Parmak izi yoksa etiketlerden deterministik
    no_fp = am_alert("x")
    del no_fp["fingerprint"]
    f1 = events.parse_alertmanager(am_payload(no_fp))[0].fingerprint
    f2 = events.parse_alertmanager(am_payload(no_fp))[0].fingerprint
    assert f1 == f2 and len(f1) == 32


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("crit", "critical"), ("error", "high"), ("P2", "high"), ("warn", "warning"),
     ("info", "info"), ("bilinmez", "warning"), (None, "warning")],
)  # fmt: skip
def test_normalize_severity(raw, expected):
    assert events.normalize_severity(raw).value == expected


def test_parse_generic():
    [a] = events.parse_generic(
        {
            "events": [
                {
                    "source": "zabbix",
                    "status": "resolved",
                    "severity": "average",
                    "title": "Disk dolu 0555 123 45 67",
                    "labels": {"host": "web-01", "api_key": "s3cr3t"},
                    "starts_at": "2026-10-13T08:00:00Z",
                    "ends_at": "2026-10-13T08:30:00+03:00",
                }
            ]
        },
        now=T0,
    )
    assert a.source == "zabbix" and a.status == EventStatus.RESOLVED
    assert a.severity == EventSeverity.WARNING and a.labels["api_key"] == "***"
    assert "0555 123 45 67" not in a.title
    assert a.ends_at == datetime(2026, 10, 13, 5, 30, tzinfo=UTC)
    with pytest.raises(events.EventPayloadError):
        events.parse_generic({"events": [{"source": "Büyük Harf"}]})
    with pytest.raises(events.EventPayloadError):
        events.parse_generic({"events": []})


# --- 3) CI eşleme --------------------------------------------------------------------------------


async def test_ci_mapping_default_and_configurable_rules(session):
    host = await ci(session, "db-host-01", CIClass.SERVER, 3)
    ext = await ci(session, "odeme-api", CIClass.APPLICATION, 2)
    await cmdb.update_ci(
        session, ext, CIUpdate(external_refs={"prometheus": "payments-job"}), "mudur"
    )
    resolver = events.CIResolver(session, LIVE.events_ci_mapping)
    assert await resolver.resolve({"instance": "DB-HOST-01:9100"}) == (host, "instance:name")
    assert await resolver.resolve({"ci_id": host}) == (host, "ci_id:id")
    assert await resolver.resolve({"job": "payments-job"}) == (None, None)

    rules = [EventCIRule(label="job", match="external_ref", key="prometheus")]
    custom = events.CIResolver(session, rules)
    assert await custom.resolve({"job": "payments-job"}) == (ext, "job:external_ref")
    assert await custom.resolve({"instance": "db-host-01"}) == (None, None)
    # Ortam değişkeninden JSON olarak yapılandırılabilir
    parsed = Settings(
        events_ci_mapping=[{"label": "host", "match": "attribute", "key": "fqdn"}]
    ).events_ci_mapping
    assert parsed[0].match == "attribute" and parsed[0].key == "fqdn"


# --- 4) bağıntı: CMDB zinciri, tekilleştirme, çırpınma ----------------------------------------------


async def test_correlation_groups_chain_under_root_dependency(session):
    ids = await chain(session)
    # Önce dependency (db-host), ardından bağımlılar: hepsi tek grupta, kök host
    await _ingest(session, am_alert("fp-host", ci="db-host-01"), at=T0)
    await _ingest(
        session,
        am_alert("fp-db", ci="odeme-db", starts=T0 + timedelta(minutes=1)),
        am_alert("fp-app", ci="odeme-api", starts=T0 + timedelta(minutes=2)),
        am_alert("fp-svc", ci="odeme-hizmeti", starts=T0 + timedelta(minutes=3)),
        at=T0 + timedelta(minutes=3),
    )
    groups = (await session.scalars(select(EventGroup))).all()
    assert len(groups) == 1
    [group] = groups
    assert group.root_ci_id == ids["host"] and group.event_count == 4
    corr = await events.correlation(session, group, settings=LIVE, now=T0 + timedelta(minutes=4))
    paths = {m.ci_name: m.path for m in corr.members}
    assert paths["db-host-01"] == ["db-host-01"]
    assert paths["odeme-hizmeti"] == ["db-host-01", "odeme-db", "odeme-api", "odeme-hizmeti"]
    assert corr.proposed_priority == "P1"  # kritiklik 1 hizmet etkileniyor + kritik önem

    # İlgisiz CI ayrı grup; pencere dışındaki uyarı yeni grup
    await _ingest(session, am_alert("fp-other", ci="yazici-sunucu"), at=T0 + timedelta(minutes=5))
    late = T0 + timedelta(minutes=LIVE.events_correlation_window_minutes + 10)
    await _ingest(session, am_alert("fp-app2", ci="odeme-api", starts=late), at=late)
    assert await _count(session, EventGroup) == 3


async def test_correlation_reroots_when_dependency_alert_arrives_later(session):
    ids = await chain(session)
    await _ingest(session, am_alert("fp-svc", ci="odeme-hizmeti"), at=T0)
    await _ingest(
        session, am_alert("fp-app", ci="odeme-api", starts=T0 + timedelta(minutes=1)),
        at=T0 + timedelta(minutes=1),
    )  # fmt: skip
    groups = (await session.scalars(select(EventGroup))).all()
    assert len(groups) == 1 and groups[0].root_ci_id == ids["app"]
    # db-host uyarısı tüm grubu açıklıyor: yeniden köklenir
    await _ingest(
        session, am_alert("fp-host", ci="db-host-01", starts=T0 + timedelta(minutes=2)),
        at=T0 + timedelta(minutes=2),
    )  # fmt: skip
    open_groups = (
        await session.scalars(select(EventGroup).where(EventGroup.state == EventGroupState.OPEN))
    ).all()
    assert len(open_groups) == 1 and open_groups[0].root_ci_id == ids["host"]
    assert open_groups[0].event_count == 3
    actions_ = (await session.scalars(select(AuditLog.action))).all()
    assert "event.group.reroot" in actions_


async def test_correlation_merges_sibling_groups_explained_by_new_root(session):
    ids = await chain(session)
    other_app = await ci(session, "fatura-api", CIClass.APPLICATION, 3)
    await cmdb.add_relationship(session, other_app, ids["db"], "depends_on", "mudur")
    await _ingest(session, am_alert("fp-a", ci="odeme-api"), at=T0)
    await _ingest(
        session, am_alert("fp-b", ci="fatura-api", starts=T0 + timedelta(minutes=1)),
        at=T0 + timedelta(minutes=1),
    )  # fmt: skip
    assert await _count(session, EventGroup) == 2
    res = await _ingest(
        session, am_alert("fp-db", ci="odeme-db", starts=T0 + timedelta(minutes=2)),
        at=T0 + timedelta(minutes=2),
    )  # fmt: skip
    [root_group] = res.group_ids
    group = await session.get(EventGroup, root_group)
    assert group.root_ci_id == ids["db"] and group.event_count == 3
    merged = (
        await session.scalars(select(EventGroup).where(EventGroup.state == EventGroupState.MERGED))
    ).all()
    assert len(merged) == 1 and merged[0].merged_into_id == group.id


async def test_identical_fingerprint_dedup_and_resolve(session):
    await chain(session)
    a = am_alert("fp-dup", ci="odeme-db")
    r1 = await _ingest(session, a, at=T0)
    r2 = await _ingest(session, a, at=T0 + timedelta(seconds=30))
    assert (r1.created, r2.created, r2.updated) == (1, 0, 1)
    event = await session.scalar(select(Event))
    assert event.received_count == 2 and await _count(session, Event) == 1
    # Çözülme aynı satırı günceller; eski "firing" yeniden oynatılırsa yok sayılır
    resolved = am_alert("fp-dup", status="resolved", ci="odeme-db", ends=T0 + timedelta(minutes=3))
    await _ingest(session, resolved, at=T0 + timedelta(minutes=4))
    await session.refresh(event)
    assert event.status == EventStatus.RESOLVED
    r3 = await _ingest(session, a, at=T0 + timedelta(minutes=5))
    assert r3.ignored == 1
    group = await session.get(EventGroup, event.group_id)
    assert group.state == EventGroupState.RESOLVED and group.resolved_at is not None
    # Aynı parmak izi yeni starts_at ile yeniden ateşleniyor: yeni satır
    refire = am_alert("fp-dup", ci="odeme-db", starts=T0 + timedelta(minutes=6))
    r4 = await _ingest(session, refire, at=T0 + timedelta(minutes=6))
    assert r4.created == 1 and await _count(session, Event) == 2


async def test_flapping_is_suppressed(session):
    await ci(session, "kararsiz-sunucu", CIClass.SERVER, 3)
    settings = LIVE.model_copy(update={"events_flap_threshold": 3})
    for i in range(4):
        start = T0 + timedelta(minutes=2 * i)
        await _ingest(
            session, am_alert("fp-flap", ci="kararsiz-sunucu", starts=start),
            at=start, settings=settings,
        )  # fmt: skip
        await _ingest(
            session,
            am_alert("fp-flap", status="resolved", ci="kararsiz-sunucu", starts=start,
                     ends=start + timedelta(minutes=1)),
            at=start + timedelta(minutes=1), settings=settings,
        )  # fmt: skip
    rows = (await session.scalars(select(Event).order_by(Event.starts_at))).all()
    assert [r.suppressed for r in rows] == [None, None, "flapping", "flapping"]
    # Çırpınan (bastırılmış) uyarı olay açmaz
    start = T0 + timedelta(minutes=9)
    res = await _ingest(
        session, am_alert("fp-flap", ci="kararsiz-sunucu", starts=start), at=start,
        settings=settings,
    )  # fmt: skip
    group = await session.get(EventGroup, res.group_ids[0])
    assert group.flapping is True
    [out] = await event_agent.process_groups(session, res.group_ids, settings=settings, now=start)
    assert out.action == "skipped" and await _count(session, Incident) == 0


# --- 5) olay açma: grup başına tek olay ------------------------------------------------------------


async def test_group_creates_one_incident_and_notes_updates(session):
    await ci(session, "rapor-sunucu", CIClass.SERVER, 4)
    await ci(session, "rapor-db", CIClass.DATABASE, 4)
    res = await _ingest(session, am_alert("fp-r1", severity="error", ci="rapor-sunucu"), at=T0)
    [out] = await event_agent.process_groups(session, res.group_ids, settings=LIVE, now=T0)
    assert out.action == "create" and out.status == "executed"
    incident = await session.get(Incident, out.incident_id)
    assert incident.priority.value == "P4" and incident.caller == "system:izleme"
    assert incident.title.startswith("[İzleme] rapor-sunucu")
    # Aynı grup tekrar işlenirse ikinci olay yok
    await event_agent.process_groups(session, res.group_ids, settings=LIVE, now=T0)
    assert await _count(session, Incident) == 1

    # Aynı kök CI'da yeni uyarı aynı gruba -> mevcut olaya not (yeni olay yok)
    later = T0 + timedelta(minutes=2)
    res2 = await _ingest(
        session, am_alert("fp-r2", severity="error", alertname="DiskFull", ci="rapor-sunucu",
                          starts=later), at=later,
    )  # fmt: skip
    assert res2.group_ids == res.group_ids
    [out2] = await event_agent.process_groups(session, res2.group_ids, settings=LIVE, now=later)
    assert out2.action == "update" and out2.status == "executed"
    assert await _count(session, Incident) == 1
    notes = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "incident.work_note"))
    ).all()
    assert len(notes) == 1 and "DiskFull" in notes[0].details["note"]
    assert notes[0].actor == "ai:events"
    # Değişiklik yoksa yeni not yok
    await event_agent.process_groups(session, res2.group_ids, settings=LIVE, now=later)
    assert await _count(session, AuditLog, AuditLog.action == "incident.work_note") == 1


async def test_below_threshold_and_shadow_mode(session):
    await ci(session, "uyari-sunucu", CIClass.SERVER, 4)
    res = await _ingest(session, am_alert("fp-w", severity="warning", ci="uyari-sunucu"), at=T0)
    [out] = await event_agent.process_groups(session, res.group_ids, settings=LIVE, now=T0)
    assert out.action == "skipped" and "eşik" in out.reason
    # Eşik yapılandırılabilir; gölge modda yalnızca öneri (olay açılmaz)
    shadow = EV_SETTINGS.model_copy(
        update={"events_incident_min_severity": "warning", "ai_shadow_mode": True}
    )
    [out] = await event_agent.process_groups(session, res.group_ids, settings=shadow, now=T0)
    assert out.action == "create" and out.status != "executed"
    assert await _count(session, Incident) == 0


async def test_p1_group_needs_approval_then_opens_once_with_actor(session):
    ids = await chain(session)
    res = await _ingest(session, am_alert("fp-host", ci="db-host-01"), at=T0)
    [out] = await event_agent.process_groups(session, res.group_ids, settings=LIVE, now=T0)
    assert out.status == "pending_approval" and await _count(session, Incident) == 0
    ar = await session.get(ActionRequest, out.action_request_id)
    assert ar.risk_class == "R2" and ar.requested_by == "ai:events"
    # İnsan onayı: olay onaylayanın kimliğiyle açılır, öncelik mevcut kuraldan (P1)
    ar = await actions.approve(session, ar.id, HUMAN, settings=LIVE, now=T0)
    assert ar.state == "executed", ar.error
    group = await session.get(EventGroup, res.group_ids[0])
    incident = await session.get(Incident, group.incident_id)
    assert incident.priority.value == "P1"
    linked = {c.id for c in await incident_service.list_affected_cis(session, incident.id)}
    assert ids["host"] in linked
    # Sonraki uyarılar aynı olaya not; yeni istek/olay yok
    later = T0 + timedelta(minutes=1)
    res2 = await _ingest(session, am_alert("fp-db", ci="odeme-db", starts=later), at=later)
    [out2] = await event_agent.process_groups(session, res2.group_ids, settings=LIVE, now=later)
    assert out2.action == "update" and out2.status == "executed"
    assert await _count(session, Incident) == 1
    assert await _count(session, ActionRequest, ActionRequest.tool == "event.create_incident") == 1


async def test_rejected_create_request_is_not_reproposed(session):
    await chain(session)
    res = await _ingest(session, am_alert("fp-host", ci="db-host-01"), at=T0)
    [out] = await event_agent.process_groups(session, res.group_ids, settings=LIVE, now=T0)
    await actions.reject(session, out.action_request_id, HUMAN, "Bakım çalışması", now=T0)
    [again] = await event_agent.process_groups(session, res.group_ids, settings=LIVE, now=T0)
    assert again.action == "skipped" and "reddedildi" in again.reason
    assert await _count(session, ActionRequest) == 1


async def test_silenced_group_suppresses_new_alerts(session):
    await ci(session, "bakim-sunucu", CIClass.SERVER, 4)
    res = await _ingest(session, am_alert("fp-s1", severity="error", ci="bakim-sunucu"), at=T0)
    group = await session.get(EventGroup, res.group_ids[0])
    await events.silence(session, group, "sef.ali", minutes=60, comment="Bakım", now=T0)
    later = T0 + timedelta(minutes=5)
    res2 = await _ingest(
        session, am_alert("fp-s2", severity="error", ci="bakim-sunucu", starts=later), at=later
    )
    assert res2.group_ids == [group.id]
    new = await session.scalar(select(Event).where(Event.fingerprint == "fp-s2"))
    assert new.suppressed == "silenced"
    [out] = await event_agent.process_groups(session, res2.group_ids, settings=LIVE, now=later)
    assert out.action == "skipped" and out.reason == "susturuldu"
    assert await _count(session, Incident) == 0


# --- 6) uçtan uca webhook + okuma API'si ----------------------------------------------------------


async def test_webhook_end_to_end_and_read_api(client, session):
    await ci(session, "api-sunucu", CIClass.SERVER, 4)
    await session.commit()
    now = _now().replace(microsecond=0)
    payload = am_payload(am_alert("fp-e2e", severity="error", instance="api-sunucu:9100",
                                  starts=now - timedelta(minutes=1)))  # fmt: skip
    body = body_of(payload)
    r = await client.post("/events/alertmanager", content=body, headers=signed_headers(body))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created"] == 1 and len(data["group_ids"]) == 1
    assert data["actions"][0]["action"] == "create" and data["actions"][0]["status"] == "executed"
    # Aynı uyarı yeni teslimatla (yeni zaman damgası) gelir: olay tekrar açılmaz
    body2 = body_of(payload | {"groupKey": "yeniden"})
    r = await client.post("/events/alertmanager", content=body2, headers=signed_headers(body2))
    assert r.status_code == 200 and r.json()["updated"] == 1
    assert await _count(session, Incident) == 1

    # Genel JSON gövdesi
    generic = {"events": [{"source": "zabbix", "severity": "warning", "title": "CPU yüksek",
                           "labels": {"host": "web-09"}}]}  # fmt: skip
    r = await client.post("/events/generic", content=body_of(generic), headers=token_headers())
    assert r.status_code == 200 and r.json()["created"] == 1

    r = await client.get("/events?status=firing", headers=AGENT)
    assert r.status_code == 200 and len(r.json()["items"]) == 2
    r = await client.get("/events?source=zabbix", headers=ADMIN)
    assert [i["title"] for i in r.json()["items"]] == ["CPU yüksek"]
    r = await client.get("/events/groups", headers=AGENT)
    assert r.status_code == 200 and len(r.json()["items"]) == 2
    gid = data["group_ids"][0]
    r = await client.get(f"/events/groups/{gid}", headers=AGENT)
    detail = r.json()
    assert r.status_code == 200 and detail["root_ci_name"] == "api-sunucu"
    assert detail["group"]["incident_id"] and detail["events"][0]["ci_rule"] == "instance:name"
    assert (await client.get("/events/groups/yok", headers=AGENT)).status_code == 404
    # Yalnızca agent / admin
    for headers in (APPROVER, dev_headers("vatandas", "requester")):
        assert (await client.get("/events", headers=headers)).status_code == 403
        assert (await client.get("/events/groups", headers=headers)).status_code == 403
    assert (await client.get("/events")).status_code == 401


# --- 7) insan eylemleri: üstlen (R1) / sustur (R2, onay) — portal /olaylar/:id sözleşmesi ---------


async def test_group_acknowledge_and_silence_endpoints_go_through_tool_pipeline(client, session):
    await ci(session, "kuyruk-sunucu", CIClass.SERVER, 4)
    await session.commit()
    body = body_of(_payload("fp-ack", severity="warning", ci="kuyruk-sunucu"))
    r = await client.post("/events/alertmanager", content=body, headers=signed_headers(body))
    assert r.status_code == 200, r.text
    gid = r.json()["group_ids"][0]
    base = f"/events/groups/{gid}"

    # Yalnızca agent / admin; yapay zekâ belirteci, onaylayan ve talep eden 403
    for headers in (APPROVER, dev_headers("vatandas", "requester"), dev_headers("bot", "ai_agent")):
        assert (
            await client.post(f"{base}/acknowledge", json={}, headers=headers)
        ).status_code == 403
        r = await client.post(
            f"{base}/silence", json={"minutes": 30, "comment": "x"}, headers=headers
        )
        assert r.status_code == 403
    assert (
        await client.post("/events/groups/yok/acknowledge", json={}, headers=AGENT)
    ).status_code == 404
    r = await client.post(f"{base}/silence", json={"minutes": 1, "comment": ""}, headers=AGENT)
    assert r.status_code == 422

    # Üstlen: R1, agent kimliğiyle hemen çalışır, denetlenir
    r = await client.post(f"{base}/acknowledge", json={"note": "Bakıyorum"}, headers=AGENT)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "executed" and r.json()["risk_class"] == "R1"
    session.expire_all()
    group = await session.get(EventGroup, gid)
    assert group.acknowledged_by == "ayse"

    # Sustur: R2 -> bekleyen eylem isteği; grup onaysız susturulmaz
    r = await client.post(
        f"{base}/silence", json={"minutes": 60, "comment": "Planlı bakım"}, headers=AGENT
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "pending_approval" and data["risk_class"] == "R2"
    session.expire_all()
    assert (await session.get(EventGroup, gid)).silenced_until is None
    ar = await session.get(ActionRequest, data["action_request_id"])
    assert ar.requested_by == "ayse" and ar.tool == "event.silence"
    # İsteği açan onaylayamaz; başka onaylayan onaylayınca susturulur
    r = await client.post(
        f"/actions/{ar.id}/approve", json={}, headers=dev_headers("ayse", "agent", "approver")
    )
    assert r.status_code in (403, 409), r.text
    r = await client.post(f"/actions/{ar.id}/approve", json={}, headers=APPROVER)
    assert r.status_code == 200 and r.json()["state"] == "executed", r.text
    session.expire_all()
    group = await session.get(EventGroup, gid)
    assert group.silenced_until is not None and group.silenced_by == "sef.ali"
