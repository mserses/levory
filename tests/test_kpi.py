"""Faz 4 KPI'ları (services/kpi.py): sentetik veri, duraklatılan SLA ve saat dilimi uç durumları."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from itertools import count
from zoneinfo import ZoneInfo

import httpx
import pytest
from _auth import ADMIN, AGENT, APPROVER, dev_headers
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from serviszeka import audit, db
from serviszeka.domain.enums import (
    ChangeState,
    ChangeType,
    DecisionOutcome,
    IncidentStatus,
    OnHoldReason,
    Priority,
    ProblemState,
    TicketKind,
)
from serviszeka.main import app
from serviszeka.models import (
    AIDecision,
    ChangeRequest,
    Incident,
    Problem,
    ServiceRequest,
)
from serviszeka.services import kpi, sla_forecast

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
IST = ZoneInfo("Europe/Istanbul")
H = timedelta(hours=1)
D = timedelta(days=1)
_seq = count(1)


def _inc(
    *,
    created: datetime,
    resolved: datetime | None = None,
    responded: datetime | None = None,
    priority: Priority = Priority.P3,
    group: str | None = "network_ops",
    category: str | None = "ag",
    service: str | None = "vpn",
    status: IncidentStatus | None = None,
    paused: int = 0,
    paused_at: datetime | None = None,
    kind: TicketKind = TicketKind.INCIDENT,
    response_due: datetime | None = None,
    resolve_due: datetime | None = None,
) -> Incident:
    if status is None:
        status = IncidentStatus.RESOLVED if resolved else IncidentStatus.IN_PROGRESS
    n = next(_seq)
    return Incident(
        number=f"INC{n:07d}",
        kind=kind,
        title=f"Olay {n}",
        caller="ali",
        category=category,
        service=service,
        priority=priority,
        status=status,
        assignment_group=group,
        created_at=created,
        updated_at=created,
        resolved_at=resolved,
        responded_at=responded,
        sla_paused_seconds=paused,
        sla_paused_at=paused_at,
        on_hold_reason=OnHoldReason.AWAITING_CALLER if paused_at else None,
        response_due_at=response_due,
        resolve_due_at=resolve_due,
    )


async def _add(session, *rows):
    session.add_all(rows)
    await session.flush()
    return rows


def _kpi(rep, key):
    return next(k for k in rep.kpis if k.key == key)


def _bd(value, dim):
    return {g.key: (g.value, g.count) for g in value.breakdown[dim]}


class _Frozen:
    """`audit.record` zamanını sabitler (denetim kayıtlarına dayalı KPI'lar için)."""

    def __init__(self, monkeypatch) -> None:
        self.now = NOW
        clock = self

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now

        monkeypatch.setattr(audit, "datetime", _DT)

    def at(self, ts: datetime) -> None:
        self.now = ts


@pytest.fixture
def frozen(monkeypatch) -> _Frozen:
    return _Frozen(monkeypatch)


async def _update(session, frozen, inc_id, at, changes, actor="ayse", **extra):
    frozen.at(at)
    await audit.record(
        session,
        actor=actor,
        action="incident.update",
        entity_type="incident",
        entity_id=inc_id,
        details={"changes": changes, **extra},
    )


# --- pencere ve tanımlar -------------------------------------------------------------------------


def test_window_is_half_open_utc_and_previous_has_equal_length():
    w = kpi.Window.ending(datetime(2026, 6, 30, 15, 0, tzinfo=IST), 30)
    assert w.end == NOW and w.start == NOW - 30 * D
    assert w.previous() == kpi.Window(NOW - 60 * D, NOW - 30 * D)
    # saat dilimsiz değer UTC kabul edilir
    assert kpi.Window.ending(datetime(2026, 6, 30, 12, 0), 30) == w
    assert w.has(w.start) and not w.has(w.end)
    with pytest.raises(ValueError):
        kpi.Window.ending(NOW, 0)
    with pytest.raises(ValueError):
        kpi.Window(NOW, NOW)


def test_definitions_cover_every_calculator_with_formula():
    keys = [d.key for d in kpi.definitions()]
    assert keys == list(kpi.KPI_KEYS) and set(keys) == set(kpi.CALCULATORS)
    for d in kpi.definitions():
        assert d.formula and d.name and d.better in ("lower", "higher")
    for required in (
        "mttr", "mtta", "fcr_rate", "sla_response_compliance", "sla_resolve_compliance",
        "backlog", "reopen_rate", "automation_rate", "ai_triage_acceptance",
        "change_success_rate", "emergency_change_share", "problem_known_error_lead_time",
    ):  # fmt: skip
        assert required in keys


def test_epoch_expression_compiles_per_dialect():
    stmt = select(kpi.epoch_seconds(Incident.created_at))
    pg = str(stmt.compile(dialect=postgresql.dialect()))
    lite = str(stmt.compile(dialect=sqlite.dialect()))
    assert "EXTRACT(EPOCH FROM incidents.created_at)" in pg
    assert "julianday(incidents.created_at)" in lite


def test_trend_direction_follows_polarity():
    assert kpi.trend("lower", 5, 10) == "improved"
    assert kpi.trend("lower", 10, 5) == "worsened"
    assert kpi.trend("higher", 90, 80) == "improved"
    assert kpi.trend("higher", 80, 80) == "flat"
    assert kpi.trend("higher", None, 80) == "n/a"


# --- MTTR / MTTA ---------------------------------------------------------------------------------


async def _seed_mttr(session):
    await _add(
        session,
        # geçerli pencere [31 May 12:00, 30 Haz 12:00)
        _inc(created=datetime(2026, 6, 10, tzinfo=UTC), resolved=datetime(2026, 6, 10, 10, tzinfo=UTC),
             responded=datetime(2026, 6, 10, 0, 30, tzinfo=UTC), priority=Priority.P2,
             paused=2 * 3600),  # 10 saat - 2 saat duraklatma = 8 saat
        _inc(created=datetime(2026, 6, 11, tzinfo=UTC), resolved=datetime(2026, 6, 11, 4, tzinfo=UTC),
             responded=datetime(2026, 6, 11, 0, 10, tzinfo=UTC), priority=Priority.P2),  # 4 saat
        _inc(created=datetime(2026, 6, 12, tzinfo=UTC), resolved=datetime(2026, 6, 12, 12, tzinfo=UTC),
             group="service_desk_l1", category="yazilim", service="erp"),  # 12 saat
        # tam pencere başında çözülen dahil (yarı açık aralık), sonunda çözülen hariç
        _inc(created=datetime(2026, 5, 31, 6, tzinfo=UTC), resolved=datetime(2026, 5, 31, 12, tzinfo=UTC)),
        _inc(created=datetime(2026, 6, 30, 2, tzinfo=UTC), resolved=NOW),
        # pencere başından 1 sn önce: önceki pencere (İstanbul'da 31 Mayıs 14:59:59)
        _inc(created=datetime(2026, 5, 31, 10, tzinfo=UTC),
             resolved=datetime(2026, 5, 31, 11, 59, 59, tzinfo=UTC)),
        _inc(created=datetime(2026, 5, 10, tzinfo=UTC), resolved=datetime(2026, 5, 10, 16, tzinfo=UTC),
             priority=Priority.P2),  # önceki pencere: 16 saat
        # hizmet talebi ve iptal edilen MTTR'ye girmez
        _inc(created=datetime(2026, 6, 1, tzinfo=UTC), resolved=datetime(2026, 6, 5, tzinfo=UTC),
             kind=TicketKind.SERVICE_REQUEST),
        _inc(created=datetime(2026, 6, 1, tzinfo=UTC), resolved=datetime(2026, 6, 6, tzinfo=UTC),
             status=IncidentStatus.CANCELLED),
    )  # fmt: skip


async def test_mttr_subtracts_paused_time_and_trends_against_previous_window(session):
    await _seed_mttr(session)
    rep = await kpi.report(session, end=NOW, days=30, keys=["mttr"], now=NOW)
    m = _kpi(rep, "mttr")
    # (8 + 4 + 12 + 6) / 4 = 7.5 saat; önceki: (16 + 1.99972) / 2
    assert m.count == 4 and m.value == 7.5
    assert m.details["median_hours"] == 7.0 and m.details["p90_hours"] == pytest.approx(10.8)
    assert m.previous_count == 2 and m.previous == pytest.approx(9.0, abs=1e-3)
    assert m.trend == "improved" and m.delta == pytest.approx(-1.5, abs=1e-3)
    assert m.delta_pct == pytest.approx(-16.67, abs=0.01)
    assert _bd(m, "priority") == {"P2": (6.0, 2), "P3": (9.0, 2)}
    assert _bd(m, "assignment_group") == {"network_ops": (6.0, 3), "service_desk_l1": (12.0, 1)}
    assert _bd(m, "category")["yazilim"] == (12.0, 1)
    assert _bd(m, "service")["vpn"] == (6.0, 3)


async def test_report_window_end_in_other_timezone_matches_utc(session):
    await _seed_mttr(session)
    utc = await kpi.report(session, end=NOW, days=30, keys=["mttr", "mtta"], now=NOW)
    ist = await kpi.report(
        session, end=NOW.astimezone(IST), days=30, keys=["mttr", "mtta"], now=NOW.astimezone(IST)
    )
    naive = await kpi.report(
        session, end=NOW.replace(tzinfo=None), days=30, keys=["mttr", "mtta"], now=NOW
    )
    assert utc.kpis == ist.kpis == naive.kpis
    assert ist.window.end == NOW and ist.window.end.tzinfo is not None


async def test_mtta_uses_first_response(session):
    await _seed_mttr(session)
    m = _kpi(await kpi.report(session, end=NOW, days=30, keys=["mtta"], now=NOW), "mtta")
    # 30 dk ve 10 dk
    assert m.count == 2 and m.value == pytest.approx(0.333, abs=1e-3)
    assert _bd(m, "priority") == {"P2": (pytest.approx(0.333, abs=1e-3), 2)}


async def test_mttr_reduction_is_computable_between_two_periods(session):
    # Faz 4 hedefi: "MTTR düştü" veriden ölçülebilir (aynı uzunlukta iki dönem)
    for day in range(1, 11):
        await _add(session, _inc(created=NOW - 45 * D + day * D,
                                 resolved=NOW - 45 * D + day * D + 10 * H))  # fmt: skip
        await _add(session, _inc(created=NOW - 15 * D + day * D,
                                 resolved=NOW - 15 * D + day * D + 6 * H))  # fmt: skip
    m = _kpi(await kpi.report(session, end=NOW, days=30, keys=["mttr"], now=NOW), "mttr")
    assert (m.value, m.previous, m.delta_pct, m.trend) == (6.0, 10.0, -40.0, "improved")


# --- SLA uyumu -----------------------------------------------------------------------------------


async def _seed_sla(session):
    t = datetime(2026, 6, 10, tzinfo=UTC)
    await _add(
        session,
        # A: 2 saat duraklatma -> çözüm vadesi 8+2 saat ötelenmiş; tam vadede çözüldü = uyumlu
        _inc(created=t, responded=t + 10 * timedelta(minutes=1), resolved=t + 10 * H,
             priority=Priority.P2, paused=7200, response_due=t + 30 * timedelta(minutes=1),
             resolve_due=t + 10 * H),
        # B: yanıt geç (ihlal), çözüm vadesinde
        _inc(created=t + D, responded=t + D + 60 * timedelta(minutes=1), resolved=t + D + 4 * H,
             priority=Priority.P2, response_due=t + D + 30 * timedelta(minutes=1),
             resolve_due=t + D + 8 * H),
        # I: P1, hiç yanıtlanmadı; iki vade de pencere içinde geçti -> iki ihlal
        _inc(created=t + 10 * D, priority=Priority.P1, group="security_ops",
             status=IncidentStatus.NEW, response_due=t + 10 * D + 15 * timedelta(minutes=1),
             resolve_due=t + 10 * D + 4 * H),
        # J: saat duraklamada; çözüm vadesi duraklamadan sonra -> henüz ihlal sayılmaz
        _inc(created=t + 15 * D, responded=t + 15 * D + H, status=IncidentStatus.ON_HOLD,
             paused_at=t + 15 * D + 12 * H, response_due=t + 15 * D + 4 * H,
             resolve_due=t + 16 * D),
        # K: çözüm vadesi gelecekte (pencere dışı)
        _inc(created=NOW - D, responded=NOW - D + H, response_due=NOW - D + 4 * H,
             resolve_due=NOW + D),
        # L: duraklamadan ÖNCE geçmiş vade gizlenemez -> ihlal
        _inc(created=t + 5 * D, responded=t + 5 * D + H, status=IncidentStatus.ON_HOLD,
             paused_at=t + 7 * D, response_due=t + 5 * D + 4 * H, resolve_due=t + 6 * D),
        # İptal edilen sayılmaz
        _inc(created=t + 2 * D, status=IncidentStatus.CANCELLED, response_due=t + 2 * D + H,
             resolve_due=t + 3 * D),
    )  # fmt: skip


async def test_sla_compliance_counts_paused_shift_and_open_overdue(session):
    await _seed_sla(session)
    rep = await kpi.sla_report(session, end=NOW, days=30, now=NOW)
    assert [k.key for k in rep.kpis] == ["sla_response_compliance", "sla_resolve_compliance"]
    resp = _kpi(rep, "sla_response_compliance")
    # uyumlu: A, J, K, L; ihlal: B (geç), I (yanıtsız)
    assert (resp.details["met"], resp.details["breached"], resp.value) == (4, 2, 66.67)
    res = _kpi(rep, "sla_resolve_compliance")
    # uyumlu: A (duraklatma ötelemesiyle), B; ihlal: I, L; J/K değerlendirilmez
    assert (res.details["met"], res.details["breached"], res.value) == (2, 2, 50.0)
    assert _bd(res, "priority") == {"P1": (0.0, 1), "P2": (100.0, 2), "P3": (0.0, 1)}
    assert _bd(res, "assignment_group")["security_ops"] == (0.0, 1)


async def test_sla_breach_not_counted_before_due_passes(session):
    await _seed_sla(session)
    # Pencere sonu I'nın çözüm vadesinden önce: I yalnızca yanıtta ihlal
    end = datetime(2026, 6, 20, 2, tzinfo=UTC)
    rep = await kpi.sla_report(session, end=end, days=30, now=end)
    res = _kpi(rep, "sla_resolve_compliance")
    assert res.details["breached"] == 1  # yalnızca L


# --- backlog -------------------------------------------------------------------------------------


async def test_backlog_open_at_window_ends_with_aging(session):
    await _add(
        session,
        _inc(created=NOW - 10.5 * D, priority=Priority.P1),  # 7-30g
        _inc(created=NOW - 5.5 * D, status=IncidentStatus.ON_HOLD),  # 3-7g
        _inc(created=NOW - 1.5 * D),  # 1-3g
        _inc(created=NOW - 2 * H, status=IncidentStatus.NEW),  # 0-1g
        # önceki pencere sonunda açıktı, şimdi çözülmüş
        _inc(created=NOW - 40 * D, resolved=NOW - 25 * D),
        # önceki pencere sonunda tam çözülmüş (resolved_at == T) açık sayılmaz
        _inc(created=NOW - 31 * D, resolved=NOW - 30 * D),
        _inc(created=NOW - 3 * D, status=IncidentStatus.CANCELLED),
    )
    b = _kpi(await kpi.report(session, end=NOW, days=30, keys=["backlog"], now=NOW), "backlog")
    assert (b.value, b.previous, b.trend) == (4.0, 1.0, "worsened")
    assert b.details["aging"] == {"0-1g": 1, "1-3g": 1, "3-7g": 1, "7-30g": 1, "30g+": 0}
    assert b.details["oldest_age_hours"] == pytest.approx(252.0)
    assert _bd(b, "priority") == {"P1": (1.0, 1), "P3": (3.0, 3)}


# --- denetime dayalı: FCR, yeniden açılma, yapay zekâ kabulü -------------------------------------


async def test_fcr_and_reopen_from_audit_history(session, frozen):
    t = datetime(2026, 6, 10, tzinfo=UTC)
    a, b, c = await _add(
        session,
        _inc(created=t, resolved=t + 5 * H),
        _inc(created=t, resolved=t + 6 * H, group="service_desk_l1"),
        _inc(created=t, resolved=t + 9 * H),
    )
    # a: ilk atama (boştan) yeniden atama değildir
    await _update(session, frozen, a.id, t + H, {"assignment_group": [None, "network_ops"]})
    await _update(session, frozen, a.id, t + 5 * H, {"status": ["in_progress", "resolved"]})
    # b: gruplar arası aktarım
    await _update(session, frozen, b.id, t + H, {"assignment_group": [None, "network_ops"]})
    await _update(session, frozen, b.id, t + 2 * H,
                  {"assignment_group": ["network_ops", "service_desk_l1"]})  # fmt: skip
    await _update(session, frozen, b.id, t + 6 * H, {"status": ["in_progress", "resolved"]})
    # c: çözüldü, yeniden açıldı, yine çözüldü
    await _update(session, frozen, c.id, t + 2 * H, {"status": ["new", "resolved"]})
    await _update(session, frozen, c.id, t + 3 * H, {"status": ["resolved", "in_progress"],
                                                      "resolved_at": ["x", None]})  # fmt: skip
    await _update(session, frozen, c.id, t + 9 * H, {"status": ["in_progress", "resolved"]})
    # pencere öncesinde kapatma (resolved -> closed) yeniden açılma değildir
    await _update(session, frozen, c.id, NOW - 40 * D, {"status": ["resolved", "closed"]})
    rep = await kpi.report(session, end=NOW, days=30, keys=["fcr_rate", "reopen_rate"], now=NOW)
    fcr = _kpi(rep, "fcr_rate")
    assert (fcr.value, fcr.count) == (33.33, 3)
    assert fcr.details == {"first_contact": 1, "reassigned": 1, "reopened": 1}
    reopen = _kpi(rep, "reopen_rate")
    assert (reopen.value, reopen.count, reopen.details["reopened"]) == (33.33, 3, 1)
    assert reopen.previous is None and reopen.trend == "n/a"


def _decision(inc, outcome, created, *, applied=(), proposed=None, approved_by=None):
    return AIDecision(
        incident_id=inc.id,
        agent="service_desk",
        action="classify",
        input_summary="",
        output={"applied_fields": list(applied), "proposed": proposed or {}},
        model="fake",
        confidence=0.9,
        outcome=outcome,
        created_at=created,
        approved_by=approved_by,
        approved_at=created + H if approved_by else None,
    )


async def test_ai_triage_acceptance_detects_later_human_override(session, frozen):
    t = datetime(2026, 6, 10, tzinfo=UTC)
    incs = await _add(session, *[_inc(created=t) for _ in range(6)])
    d1 = _decision(
        incs[0], DecisionOutcome.AUTO_APPLIED, t, applied=["category", "assignment_group"]
    )
    d2 = _decision(incs[1], DecisionOutcome.AUTO_APPLIED, t, applied=["impact"])
    d3 = _decision(incs[2], DecisionOutcome.SUGGESTED, t, proposed={"category": "ag"})
    d4 = _decision(incs[3], DecisionOutcome.NEEDS_APPROVAL, t, proposed={"urgency": "high"},
                   approved_by="sef.ali")  # fmt: skip
    d5 = _decision(incs[4], DecisionOutcome.AUTO_APPLIED, t, applied=["service"])
    d6 = _decision(incs[5], DecisionOutcome.ESCALATED, t)
    old = _decision(incs[0], DecisionOutcome.AUTO_APPLIED, NOW - 45 * D, applied=["category"])
    await _add(session, d1, d2, d3, d4, d5, d6, old)
    # yapay zekânın kendi uygulaması ve onay güncellemesi insan düzeltmesi değildir
    await _update(session, frozen, incs[0].id, t, {"category": [None, "ag"]},
                  actor="ai:service_desk", source="ai_triage")  # fmt: skip
    await _update(session, frozen, incs[3].id, t + H, {"urgency": ["low", "high"],
                  "priority": ["P5", "P3"]}, actor="sef.ali", source="ai_triage_approval")  # fmt: skip
    # d2: insan etkiyi (ve dolayısıyla önceliği) değiştirdi -> düzeltildi
    await _update(session, frozen, incs[1].id, t + 2 * H, {"impact": ["high", "low"]})
    # d5: insan yalnızca açıklamayı değiştirdi -> kabul
    await _update(session, frozen, incs[4].id, t + 2 * H, {"description": ["a", "b"]})
    # d1: karardan ÖNCEKİ insan düzenlemesi sayılmaz
    await _update(session, frozen, incs[0].id, t - H, {"category": ["x", None]})
    rep = await kpi.report(session, end=NOW, days=30, keys=["ai_triage_acceptance"], now=NOW)
    m = _kpi(rep, "ai_triage_acceptance")
    assert (m.value, m.count) == (50.0, 6)
    assert m.details["applied"] == 4 and m.details["accepted"] == 3
    assert m.details["overridden"] == 1 and m.details["override_rate"] == 25.0
    assert m.details["outcomes"] == {"auto_applied": 3, "escalated": 1, "needs_approval": 1,
                                     "suggested": 1}  # fmt: skip
    # önceki penceredeki karar sonradan (t - 1 saat) insanca düzeltildi
    assert (m.previous, m.previous_count, m.trend) == (0.0, 1, "improved")


# --- talepler, değişiklikler, sorunlar -----------------------------------------------------------


async def test_automation_rate_from_touchless_requests(session):
    from serviszeka.services import catalog

    await catalog.seed_default_items(session)
    item = await catalog.get_item(session, "password_reset")
    rows = []
    for i, (touchless, fulfilled) in enumerate(
        [(True, NOW - D), (True, NOW - 2 * D), (False, NOW - 3 * D), (None, None),
         (False, NOW - 40 * D)]
    ):  # fmt: skip
        rows.append(
            ServiceRequest(
                number=f"REQ{i + 1:07d}", catalog_item_id=item.id, item_key=item.key,
                item_name=item.name, requester="ali", opened_by="ali", touchless=touchless,
                fulfilled_at=fulfilled, created_at=NOW - 50 * D,
            )
        )  # fmt: skip
    await _add(session, *rows)
    m = _kpi(await kpi.report(session, end=NOW, days=30, keys=["automation_rate"], now=NOW),
             "automation_rate")  # fmt: skip
    assert (m.value, m.count, m.previous, m.trend) == (66.67, 3, 0.0, "improved")
    assert _bd(m, "item_key") == {"password_reset": (66.67, 3)}


def _change(n, *, created, change_type=ChangeType.NORMAL, outcome=None, ended=None):
    return ChangeRequest(
        number=f"CHG{n:07d}", title="Değişiklik", change_type=change_type,
        state=ChangeState.CLOSED if outcome == "succeeded" else ChangeState.FAILED
        if outcome else ChangeState.DRAFT,
        requested_by="ayse", implementation_outcome=outcome, actual_end=ended,
        created_at=created,
    )  # fmt: skip


async def test_change_success_and_emergency_share(session):
    await _add(
        session,
        _change(1, created=NOW - 10 * D, outcome="succeeded", ended=NOW - 5 * D),
        _change(2, created=NOW - 10 * D, outcome="succeeded", ended=NOW - 4 * D,
                change_type=ChangeType.EMERGENCY),
        _change(3, created=NOW - 10 * D, outcome="succeeded", ended=NOW - 3 * D,
                change_type=ChangeType.STANDARD),
        _change(4, created=NOW - 10 * D, outcome="failed", ended=NOW - 2 * D),
        _change(5, created=NOW - 40 * D, outcome="succeeded", ended=NOW - 39 * D),
        _change(6, created=NOW - 40 * D, outcome="failed", ended=NOW - 38 * D,
                change_type=ChangeType.EMERGENCY),
        _change(7, created=NOW - 41 * D, change_type=ChangeType.EMERGENCY),
    )  # fmt: skip
    rep = await kpi.report(
        session, end=NOW, days=30, keys=["change_success_rate", "emergency_change_share"], now=NOW
    )
    s = _kpi(rep, "change_success_rate")
    assert (s.value, s.count, s.previous, s.trend) == (75.0, 4, 50.0, "improved")
    assert _bd(s, "change_type") == {"emergency": (100.0, 1), "normal": (50.0, 2),
                                     "standard": (100.0, 1)}  # fmt: skip
    e = _kpi(rep, "emergency_change_share")
    assert (e.value, e.previous, e.trend) == (25.0, pytest.approx(66.67), "improved")


async def test_problem_to_known_error_lead_time(session, frozen):
    p1 = Problem(number="PRB0000001", title="VPN", state=ProblemState.KNOWN_ERROR,
                 created_at=datetime(2026, 6, 1, tzinfo=UTC))  # fmt: skip
    p2 = Problem(number="PRB0000002", title="E-posta", state=ProblemState.KNOWN_ERROR,
                 created_at=datetime(2026, 6, 10, tzinfo=UTC))  # fmt: skip
    p3 = Problem(number="PRB0000003", title="ERP", state=ProblemState.INVESTIGATING,
                 created_at=datetime(2026, 6, 10, tzinfo=UTC))  # fmt: skip
    await _add(session, p1, p2, p3)

    async def event(p, at, action, details):
        frozen.at(at)
        await audit.record(session, actor="ayse", action=action, entity_type="problem",
                           entity_id=p.id, details=details)  # fmt: skip

    await event(p1, datetime(2026, 6, 3, tzinfo=UTC), "problem.transition",
                {"from": "investigating", "to": "known_error"})  # fmt: skip
    await event(p1, datetime(2026, 6, 20, tzinfo=UTC), "problem.transition",
                {"from": "investigating", "to": "known_error"})  # fmt: skip
    await event(p2, datetime(2026, 6, 10, 12, tzinfo=UTC), "problem.known_error.publish",
                {"from": "investigating"})  # fmt: skip
    # zaten bilinen hatadayken yayım (from=known_error) yeni geçiş değildir
    await event(p3, datetime(2026, 6, 12, tzinfo=UTC), "problem.known_error.publish",
                {"from": "known_error"})  # fmt: skip
    await event(p3, datetime(2026, 6, 13, tzinfo=UTC), "problem.transition",
                {"from": "new", "to": "investigating"})  # fmt: skip
    m = _kpi(await kpi.report(session, end=NOW, days=30, keys=["problem_known_error_lead_time"],
                              now=NOW), "problem_known_error_lead_time")  # fmt: skip
    assert (m.value, m.count) == (30.0, 2)
    assert m.details["median_hours"] == 30.0


async def test_report_rejects_unknown_kpi_and_is_read_only(session):
    await _seed_mttr(session)
    with pytest.raises(ValueError):
        await kpi.report(session, keys=["yok"], now=NOW)
    full = await kpi.report(session, end=NOW, now=NOW)
    assert [k.key for k in full.kpis] == list(kpi.KPI_KEYS)
    assert not session.new and not session.dirty and not session.deleted


# --- API -----------------------------------------------------------------------------------------


@pytest.fixture
async def client(session) -> AsyncIterator[httpx.AsyncClient]:
    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    await _seed_mttr(session)
    await session.commit()
    app.dependency_overrides[db.get_session] = _get_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def test_api_kpi_roles_and_params(client):
    params = {"end": "2026-06-30T15:00:00+03:00", "days": 30, "kpi": ["mttr", "backlog"]}
    for headers in (AGENT, APPROVER, ADMIN):
        r = await client.get("/kpi", params=params, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [k["key"] for k in body["kpis"]] == ["mttr", "backlog"]
        assert body["window"]["end"].startswith("2026-06-30T12:00:00")
        assert body["kpis"][0]["value"] == 7.5
    for headers in (dev_headers("son.kullanici", "requester"), dev_headers("copilot", "ai_agent")):
        assert (await client.get("/kpi", headers=headers)).status_code == 403
        assert (await client.get("/sla/forecast", headers=headers)).status_code == 403
        assert (await client.get("/sla/report", headers=headers)).status_code == 403
    assert (await client.get("/kpi", params={"kpi": "yok"}, headers=AGENT)).status_code == 422
    assert (await client.get("/kpi", params={"days": 0}, headers=AGENT)).status_code == 422
    r = await client.get("/kpi/definitions", headers=APPROVER)
    assert r.status_code == 200 and len(r.json()) == len(kpi.KPI_KEYS)


async def test_api_sla_report_and_forecast(client):
    r = await client.get("/sla/report", params={"end": "2026-06-30T12:00:00Z"}, headers=AGENT)
    assert r.status_code == 200
    assert [k["key"] for k in r.json()["kpis"]] == list(kpi.SLA_KPI_KEYS)
    r = await client.get("/sla/forecast", params={"min_probability": 0.5}, headers=APPROVER)
    assert r.status_code == 200 and r.json()["count"] == len(r.json()["items"])
    r = await client.get(
        "/sla/forecast/backtest",
        params={"split_at": "2026-06-01T00:00:00Z", "end": "2026-06-30T12:00:00Z"},
        headers=AGENT,
    )
    assert r.status_code == 200 and "brier" in r.json()
    r = await client.get(
        "/sla/forecast/backtest",
        params={"split_at": "2026-07-01T00:00:00Z", "end": "2026-06-30T12:00:00Z"},
        headers=AGENT,
    )
    assert r.status_code == 422


# --- PostgreSQL (isteğe bağlı: SZ_TEST_POSTGRES_URL) ----------------------------------------------

PG_URL = os.environ.get("SZ_TEST_POSTGRES_URL")


@pytest.mark.skipif(not PG_URL, reason="SZ_TEST_POSTGRES_URL tanımlı değil")
async def test_postgres_report_matches_sqlite(session, frozen):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from serviszeka.services.sla import ensure_utc

    await _seed_mttr(session)
    await _seed_sla(session)
    await session.commit()
    keys = list(kpi.KPI_KEYS)
    lite = await kpi.report(session, end=NOW, days=30, keys=keys, now=NOW)

    def _aware(v):
        return ensure_utc(v) if isinstance(v, datetime) else v

    rows = [
        {c.name: _aware(getattr(r, c.name)) for c in Incident.__table__.columns}
        for r in (await session.scalars(select(Incident))).all()
    ]
    engine = create_async_engine(PG_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
            await conn.run_sync(db.Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as pg:
            # Oturum saat dilimi UTC dışında olsa da sonuç aynı olmalı (timestamptz)
            await pg.execute(text("SET TIME ZONE 'Asia/Tokyo'"))
            pg.add_all(Incident(**row) for row in rows)
            await pg.flush()
            got = await kpi.report(pg, end=NOW.astimezone(IST), days=30, keys=keys, now=NOW)
            fc = await sla_forecast.forecast(pg, now=NOW, min_sample=1)
            await pg.rollback()
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await engine.dispose()
    assert [(k.key, k.value, k.count, k.previous, k.details) for k in got.kpis] == [
        (k.key, k.value, k.count, k.previous, k.details) for k in lite.kpis
    ]
    lite_fc = await sla_forecast.forecast(session, now=NOW, min_sample=1)
    assert [(i.number, i.probability) for i in fc.items] == [
        (i.number, i.probability) for i in lite_fc.items
    ]
