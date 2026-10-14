"""Faz 4 karşı-inceleme (adversarial review) gerilemeleri.

Her test önce başarısız yazıldı, ardından düzeltildi:

1. HMAC imzalı teslimat, tekilleştirme kaydı silindikten sonra (ileri tarihli zaman damgası hâlâ
   geçerliyken) yeniden oynatılamaz.
2. Aşırı iç içe JSON gövdesi 500 değil 422 döner.
3. Paylaşılan gizli (imzasız) kipte yanlış belirteç gövde okunmadan 401 alır.
4. İzleme kaynaklı olayın arayanı (caller) bir kullanıcı adıyla çakışmaz: "izleme" adlı
   self-servis kullanıcı izleme olaylarını göremez.
5. Olay fırtınası: olay ajanı saatte en fazla `SZ_EVENTS_MAX_INCIDENTS_PER_HOUR` olay açar.
6. SLA tahmini geriye dönük sınamasında test kümesi sonuca göre seçilmez: bitişte hâlâ açık ama
   bütçesini aşmış olay ihlal olarak sayılır (yalnızca çözülenleri saymak iyimser yanlılık).
7. Yapay zekâ (`ai:csi` / MCP istemcisi) iyileştirme açarken taban değeri uyduramaz; taban
   yalnızca KPI'dan hesaplanır (MTTR iddiası yalnızca veriden).
8. Geçmiş bir an için SLA tahmini o andan sonra çözülen olayların süresini kullanmaz.
9. Zamanlayıcı kirası iş zaman aşımından uzundur (iptal gecikmesinde ikinci kopya çalışmaz).
10. Grup numarası tüm numaralar okunmadan üretilir (performans; davranış korunur).
11. Çözülen grup, olayı hâlâ açıkken yeniden ateşlenince ikinci olay açılmaz.
12. KPI / SLA uçlarında takvim dışına taşan tarih 500 değil 422.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _auth import dev_headers
from _csi import AI_CSI, NOW, D, Frozen, mttr_rows
from _events import (
    EV_SETTINGS,
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
from serviszeka.config import Settings
from serviszeka.domain.enums import CIClass, IncidentStatus, Priority, TicketKind
from serviszeka.main import app
from serviszeka.models import Event, EventDelivery, Improvement, Incident
from serviszeka.services import events
from serviszeka.services import sla_forecast as sf
from serviszeka.tools import ToolStatus, execute_tool

LIVE = EV_SETTINGS.model_copy(update={"ai_shadow_mode": False, "ai_confidence_threshold": 0.0})
H = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(UTC)


async def _count(session, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for w in where:
        stmt = stmt.where(w)
    return int(await session.scalar(stmt) or 0)


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


# --- 1) HMAC yeniden oynatma ---------------------------------------------------------------------


async def test_signed_delivery_not_replayable_after_dedup_record_expiry(session):
    window = LIVE.events_replay_window_seconds
    body = body_of(am_payload(am_alert("fp-replay-future", ci="yok")))
    # Göndericinin saati ileride: zaman damgası pencerenin ucunda (kabul edilir)
    ts = int((T0 + timedelta(seconds=window - 1)).timestamp())
    headers = httpx.Headers(signed_headers(body, ts=ts))
    auth = events.authenticate_webhook(LIVE, headers, body, source="alertmanager", now=T0)
    assert await events.register_delivery(
        session, auth, source="alertmanager", now=T0, settings=LIVE
    )
    # Pencere kadar süre geçti; zaman damgası hâlâ geçerli (|şimdi - ts| < pencere)
    later = T0 + timedelta(seconds=window + 30)
    replay = events.authenticate_webhook(LIVE, headers, body, source="alertmanager", now=later)
    assert replay.dedup_key == auth.dedup_key
    assert not await events.register_delivery(
        session, replay, source="alertmanager", now=later, settings=LIVE
    ), "imza hâlâ geçerliyken tekilleştirme kaydı silinmemeli"
    # İmzanın geçerliliği bitince kayıt temizlenebilir
    much_later = T0 + timedelta(seconds=3 * window)
    await events.register_delivery(
        session,
        events.DeliveryAuth("hmac", "x" * 64),
        source="alertmanager",
        now=much_later,
        settings=LIVE,
    )
    assert await _count(session, EventDelivery, EventDelivery.dedup_key == auth.dedup_key) == 0


# --- 2-3) gövde ayrıştırma ve kimlik sırası -------------------------------------------------------


async def test_deeply_nested_json_body_is_422_not_500(client, session):
    depth = 50_000
    body = b'{"version": "4", "alerts": ' + b"[" * depth + b"]" * depth + b"}"
    assert len(body) < LIVE.events_max_body_bytes
    r = await client.post("/events/alertmanager", content=body, headers=token_headers())
    assert r.status_code == 422, r.text
    assert await _count(session, Event) == 0


async def test_wrong_shared_secret_rejected_before_body_is_read(client, session):
    client.state["settings"] = LIVE.model_copy(update={"events_max_body_bytes": 1024})
    big = body_of(am_payload(am_alert("fp-big", note="x" * 4000)))
    r = await client.post("/events/alertmanager", content=big, headers=token_headers("yanlis"))
    assert r.status_code == 401, r.text  # 413 değil: kimliksiz istemciye gövde okunmaz
    # Doğru belirteçle sınır yine uygulanır
    r = await client.post("/events/alertmanager", content=big, headers=token_headers())
    assert r.status_code == 413


# --- 4) izleme olaylarının arayanı ---------------------------------------------------------------


async def test_requester_named_like_monitoring_caller_cannot_see_event_incidents(client, session):
    await ci(session, "rapor-sunucu", CIClass.SERVER, 4)
    await session.commit()
    now = _now().replace(microsecond=0)
    body = body_of(
        am_payload(am_alert("fp-caller", severity="error", ci="rapor-sunucu", starts=now - H))
    )
    r = await client.post("/events/alertmanager", content=body, headers=token_headers())
    assert r.status_code == 200 and r.json()["actions"][0]["status"] == "executed", r.text
    incident_id = r.json()["actions"][0]["incident_id"]
    incident = await session.get(Incident, incident_id)
    for username in ("izleme", incident.caller):
        if ":" in username:  # dev belirtecinde ':' ayraçtır; böyle bir kullanıcı adı olamaz
            continue
        headers = dev_headers(username, "requester")
        r = await client.get("/incidents", headers=headers)
        assert r.status_code == 200 and r.json() == [], username
        r = await client.get(f"/incidents/{incident_id}", headers=headers)
        assert r.status_code == 404, username


# --- 5) olay fırtınası ---------------------------------------------------------------------------


async def test_event_storm_caps_automatic_incidents_per_hour(session, monkeypatch):
    frozen = Frozen(monkeypatch)  # denetim zamanı (olay açılış kaydı) sahte saatte
    frozen.now = T0
    settings = LIVE.model_copy(update={"events_max_incidents_per_hour": 3})
    # CMDB'de eşlenmeyen 5 farklı kritik uyarı: her biri ayrı grup (P3, R1 -> otomatik açılırdı)
    alerts = [am_alert(f"fp-storm-{i}", alertname=f"Hata{i}", instance=f"bilinmeyen-{i}")
              for i in range(5)]  # fmt: skip
    res = await events.ingest(
        session, events.parse_alertmanager(am_payload(*alerts)), settings=settings, now=T0
    )
    assert len(res.group_ids) == 5
    outcomes = await event_agent.process_groups(session, res.group_ids, settings=settings, now=T0)
    created = [o for o in outcomes if o.action == "create" and o.status == "executed"]
    skipped = [o for o in outcomes if o.action == "skipped"]
    assert len(created) == 3 and len(skipped) == 2
    assert all("fırtına" in o.reason for o in skipped)
    assert await _count(session, Incident) == 3
    # Fırtınada bile P1/P2 olacak grup insan onayına önerilir (gizlenmez)
    await chain(session)
    res_p1 = await events.ingest(
        session,
        events.parse_alertmanager(am_payload(am_alert("fp-storm-p1", ci="db-host-01"))),
        settings=settings,
        now=T0,
    )
    [p1] = await event_agent.process_groups(session, res_p1.group_ids, settings=settings, now=T0)
    assert p1.action == "create" and p1.status == "pending_approval"
    # Bir saat sonra (yeniden gönderimde) kalan gruplar için olay açılabilir
    later = T0 + timedelta(minutes=61)
    frozen.now = later
    again = await event_agent.process_groups(
        session, [o.group_id for o in skipped], settings=settings, now=later
    )
    assert [o.status for o in again] == ["executed", "executed"]
    assert await _count(session, Incident) == 5


# --- 6) geriye dönük sınamada sonuca göre seçim yok ----------------------------------------------

END = NOW
SPLIT = NOW - 30 * D
_n = iter(range(1, 10_000))


def _row(created: datetime, hours: float | None, status: IncidentStatus | None = None) -> Incident:
    resolved = created + timedelta(hours=hours) if hours is not None else None
    return Incident(
        number=f"INC{next(_n):07d}",
        kind=TicketKind.INCIDENT,
        title="Olay",
        caller="ali",
        priority=Priority.P3,
        status=status or (IncidentStatus.RESOLVED if resolved else IncidentStatus.IN_PROGRESS),
        assignment_group="network_ops",
        created_at=created,
        updated_at=created,
        resolved_at=resolved,
        sla_paused_seconds=0,
        resolve_due_at=created + 24 * H,
    )


async def test_backtest_counts_known_breaches_still_open_at_end(session):
    # Eğitim: bölmeden önce açılıp çözülen 20 olay (%25 ihlal; hedef 24 sa)
    train = [_row(SPLIT - 60 * D + i * D, h) for i, h in enumerate([6] * 15 + [30] * 5)]
    # Test: bölmeden sonra açılan 4 hızlı çözülen olay (ihlal yok) ...
    fast = [_row(SPLIT + i * D, 5) for i in range(4)]
    # ... ve bitişte hâlâ açık, bütçesini çoktan aşmış 6 olay (bilinen ihlal)
    stuck = [_row(SPLIT + (i + 5) * D, None) for i in range(6)]
    # ... bitişten sonra çözülen ama bitişte zaten ihlalde olan 2 olay (bilinen ihlal)
    late = [_row(END - 3 * D, 24 * 5), _row(END - 2 * D, 24 * 4)]
    # ... bitişte açık ve henüz bütçesi dolmamış olay: sonucu bilinmiyor -> dışarıda
    unknown = [_row(END - 2 * H, None)]
    session.add_all([*train, *fast, *stuck, *late, *unknown])
    await session.flush()

    res = await sf.backtest(session, split_at=SPLIT, end=END, lookback_days=90)
    assert res.test_size == 12, res
    observed = sum(b.observed_rate * b.count for b in res.calibration if b.count)
    assert round(observed) == 8  # 6 açık + 2 geç çözülen ihlal


# --- 7) yapay zekâ taban değeri uyduramaz --------------------------------------------------------

LOOSE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0)


async def test_ai_cannot_supply_improvement_baseline(session):
    await mttr_rows(session, group="network_ops", category="ag", hours=10, start=NOW - 20 * D)
    args = {
        "title": "MTTR iyileştirme",
        "kpi_key": "mttr",
        "kpi_dimension": "assignment_group",
        "kpi_dimension_value": "network_ops",
        "baseline_value": 99.0,  # uydurma taban: iyileşme abartılır
        "target_value": 4.0,
    }
    r = await execute_tool(session, AI_CSI, "csi.create_improvement", args, settings=LOOSE,
                           now=NOW)  # fmt: skip
    assert r.status == ToolStatus.FAILED
    assert r.to_dict()["error"]["code"] == "validation_error"
    assert await _count(session, Improvement) == 0
    # Taban verilmezse KPI'dan hesaplanır (yapay zekâ için tek yol)
    args.pop("baseline_value")
    r = await execute_tool(session, AI_CSI, "csi.create_improvement", args, settings=LOOSE,
                           now=NOW)  # fmt: skip
    assert r.status == ToolStatus.EXECUTED
    imp = await session.scalar(select(Improvement))
    assert imp.baseline_value == pytest.approx(10.0, abs=0.01)


async def test_forecast_as_of_past_moment_ignores_later_resolutions(session):
    # Geçmiş bir an için tahmin (CSI sinyalleri `now` verir): o andan SONRA çözülen karşılaştırma
    # olayları o anda açıktı -> sansürlü sayılmalı; çözüm süresi geleceği sızdırmamalı
    as_of = NOW - 10 * D
    sample = [_row(as_of - 40 * D + i * D, 24 * 45) for i in range(20)]  # hepsi as_of'tan sonra
    target = _row(as_of - 2 * H, None)
    session.add_all([*sample, target])
    await session.flush()
    rep = await sf.forecast(session, now=as_of, lookback_days=90)
    item = next(i for i in rep.items if i.number == target.number)
    assert item.sample_resolved == 0 and item.basis == "none", item
    assert item.probability is None


# --- 9) zamanlayıcı kirası iş zaman aşımından uzun -----------------------------------------------


async def test_scheduler_lease_outlives_job_timeout_no_double_run(session):
    from serviszeka import scheduler

    runs: list[str] = []

    async def other_replica_job(ctx):
        runs.append("B")
        return {}

    async def long_job(ctx):
        runs.append("A")
        # A'nın işi zaman aşımı sınırında hâlâ sürüyor (iptal gecikmesi / saat kayması): B'nin
        # saati zaman aşımından 1 sn sonrasını gösteriyor -> B aynı işi çalıştırmamalı
        b = scheduler.Scheduler(
            Settings(),
            clock=lambda: T0 + timedelta(seconds=301),
            notifier_factory=lambda s: None,
            jobs=(scheduler.Job("uzun_is", 30, other_replica_job, timeout=300),),
            owner="B",
        )
        runs.append((await b.run_job("uzun_is")).status)
        return {}

    a = scheduler.Scheduler(
        Settings(),
        clock=lambda: T0,
        notifier_factory=lambda s: None,
        jobs=(scheduler.Job("uzun_is", 30, long_job, timeout=300),),
        owner="A",
    )
    assert (await a.run_job("uzun_is")).status == "ok"
    assert runs == ["A", "skipped"]


# --- 10) grup numarası tüm numaralar okunmadan (webhook başına 500 grup) -------------------------


async def test_group_number_uses_highest_without_scanning(session):
    from serviszeka.domain.enums import EventGroupState, EventSeverity
    from serviszeka.models import EventGroup

    for number in ("EVG0000005", "EVG9999999"):
        session.add(EventGroup(number=number, state=EventGroupState.OPEN, title="g",
                               severity=EventSeverity.HIGH, first_event_at=T0, last_event_at=T0,
                               last_change_at=T0))  # fmt: skip
    await session.flush()
    assert await events._next_group_number(session) == "EVG10000000"
    session.add(EventGroup(number="EVG10000000", state=EventGroupState.OPEN, title="g",
                           severity=EventSeverity.HIGH, first_event_at=T0, last_event_at=T0,
                           last_change_at=T0))  # fmt: skip
    await session.flush()
    assert await events._next_group_number(session) == "EVG10000001"


# --- 11) çözülen grup yeniden ateşlenince açık olaya bağlanır (olay spamı yok) --------------------


async def test_refire_after_resolve_reuses_open_incident_not_new_one(session):
    await ci(session, "rapor-sunucu", CIClass.SERVER, 4)

    async def fire(alert, at):
        res = await events.ingest(
            session, events.parse_alertmanager(am_payload(alert)), settings=LIVE, now=at
        )
        return res, await event_agent.process_groups(session, res.group_ids, settings=LIVE, now=at)

    first = am_alert("fp-refire", severity="error", ci="rapor-sunucu")
    res, [out] = await fire(first, T0)
    assert out.status == "executed" and await _count(session, Incident) == 1
    # Uyarı çözüldü: olay açık kalır, çözüm yalnızca önerilir (insan onayı bekliyor)
    resolved = am_alert("fp-refire", status="resolved", severity="error", ci="rapor-sunucu",
                        ends=T0 + timedelta(minutes=5))  # fmt: skip
    _, [prop] = await fire(resolved, T0 + timedelta(minutes=5))
    assert prop.action == "propose_resolve"
    incident = await session.get(Incident, out.incident_id)
    assert incident.status != IncidentStatus.RESOLVED

    # Aynı uyarı 10 dk sonra yeniden ateşleniyor (aynı parmak izi, yeni starts_at)
    at = T0 + timedelta(minutes=15)
    refire = am_alert("fp-refire", severity="error", ci="rapor-sunucu", starts=at)
    res2, [out2] = await fire(refire, at)
    assert res2.group_ids == res.group_ids, "olayı hâlâ açık grup yeniden açılmalı"
    assert out2.action == "update" and out2.status == "executed"
    # Aynı CI'da farklı uyarı da aynı olaya bağlanır
    other = am_alert("fp-refire-2", alertname="DiskFull", severity="error", ci="rapor-sunucu",
                     starts=at + timedelta(minutes=1))  # fmt: skip
    res3, _ = await fire(other, at + timedelta(minutes=1))
    assert res3.group_ids == res.group_ids
    assert await _count(session, Incident) == 1


# --- 12) KPI / tahmin uçlarında aşırı tarih 500 değil 422 ----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/kpi?end=0001-01-05T00:00:00&days=30",
        "/sla/report?end=0001-01-05T00:00:00",
        "/sla/forecast/backtest?split_at=0001-01-05T00:00:00&end=0001-02-01T00:00:00",
    ],
)
async def test_kpi_endpoints_reject_out_of_range_dates(client, url):
    from _auth import AGENT as H_AGENT

    r = await client.get(url, headers=H_AGENT)
    assert r.status_code == 422, (url, r.status_code, r.text)
