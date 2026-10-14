"""Faz 4 SLA ihlal tahmini (services/sla_forecast.py): Kaplan–Meier, geri dönüş, yük, backtest."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from itertools import count
from zoneinfo import ZoneInfo

import pytest

from serviszeka.domain.enums import IncidentStatus, OnHoldReason, Priority, TicketKind
from serviszeka.models import Incident
from serviszeka.services import sla_forecast as sf

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)
D = timedelta(days=1)
TARGET = {Priority.P1: 4 * H, Priority.P2: 8 * H, Priority.P3: 24 * H}
_seq = count(1)


def _inc(
    *,
    created: datetime,
    duration: timedelta | None = None,
    priority: Priority = Priority.P3,
    group: str | None = "network_ops",
    status: IncidentStatus | None = None,
    paused: int = 0,
    paused_at: datetime | None = None,
    kind: TicketKind = TicketKind.INCIDENT,
) -> Incident:
    resolved = created + duration + timedelta(seconds=paused) if duration is not None else None
    n = next(_seq)
    return Incident(
        number=f"INC{n:07d}",
        kind=kind,
        title="Olay",
        caller="ali",
        priority=priority,
        status=status or (IncidentStatus.RESOLVED if resolved else IncidentStatus.IN_PROGRESS),
        assignment_group=group,
        created_at=created,
        updated_at=created,
        resolved_at=resolved,
        sla_paused_seconds=paused,
        sla_paused_at=paused_at,
        on_hold_reason=OnHoldReason.AWAITING_VENDOR if paused_at else None,
        resolve_due_at=created + TARGET[priority] + timedelta(seconds=paused),
    )


async def _add(session, *rows):
    session.add_all(rows)
    await session.flush()
    return rows


async def _history(session, group="network_ops", priority=Priority.P3, start=NOW - 100 * D):
    """20 çözülmüş olay: 10 x 6 saat, 5 x 20 saat, 5 x 30 saat (hedef 24 saat -> %25 ihlal)."""
    durations = [6] * 10 + [20] * 5 + [30] * 5
    rows = [
        _inc(created=start + i * 4 * D, duration=h * H, priority=priority, group=group)
        for i, h in enumerate(durations)
    ]
    return await _add(session, *rows)


def _item(rep, number):
    return next(i for i in rep.items if i.number == number)


# --- Kaplan–Meier --------------------------------------------------------------------------------


def test_kaplan_meier_handles_censoring_and_ties():
    km = sf.kaplan_meier([(1, True), (2, False), (3, True), (4, True)])
    assert km.at(0) == 1.0 and km.at(1) == 0.75 and km.at(2.5) == 0.75
    assert km.at(3) == pytest.approx(0.375) and km.at(4) == 0.0
    assert (km.n, km.events) == (4, 3)
    tied = sf.kaplan_meier([(5, True), (5, True), (5, False), (9, True)])
    assert tied.at(5) == pytest.approx(0.5) and tied.at(9) == 0.0
    # yalnızca sansürlü gözlemler: eğri düşmez
    assert sf.kaplan_meier([(3, False), (4, False)]).at(10) == 1.0
    assert km.conditional_median(0) == 3  # S(3)=0.375 <= 0.5
    assert km.conditional_median(3.5) == 4
    assert sf.kaplan_meier([(1, True)]).conditional_median(2) is None


def test_brier_and_calibration_bins():
    pairs = [(0.1, 0), (0.9, 1), (0.5, 1), (1.0, 1)]
    assert sf.brier(pairs) == pytest.approx((0.01 + 0.01 + 0.25 + 0) / 4)
    bins = sf.calibration(pairs)
    assert [b.count for b in bins] == [1, 0, 1, 0, 2]
    assert bins[4].observed_rate == 1.0 and bins[1].mean_predicted is None
    assert sf.brier([]) is None


# --- tahmin --------------------------------------------------------------------------------------


async def test_forecast_conditions_on_age_and_explains(session):
    await _history(session)
    (young, old) = await _add(
        session,
        _inc(created=NOW - 1 * H, group="network_ops"),
        _inc(created=NOW - 12 * H, group="network_ops"),
    )
    rep = await sf.forecast(session, now=NOW)
    assert rep.count == 2 and rep.min_sample == sf.MIN_SAMPLE
    o = _item(rep, old.number)
    assert o.basis == "priority_group"
    # örneklem: 20 çözülmüş + diğer açık olay (sansürlü, kendisi hariç)
    assert (o.sample_size, o.sample_resolved, o.sample_censored) == (21, 20, 1)
    assert o.elapsed_hours == 12.0 and o.remaining_hours == 12.0 and o.target_hours == 24.0
    # Koşullu: S(24)/S(12) = (21·0.25+.5)/(21·0.5+.5) (genç olay sansürlü 1 saat: risk kümesi 21)
    km_n = 21
    s24, s12 = 5 / 20, 10 / 20  # 1 saatlik sansür 6 saatlik olaylardan önce: oranlar değişmez
    cond = ((km_n * s24 + 0.5) / (km_n + 1)) / ((km_n * s12 + 0.5) / (km_n + 1))
    # Yük: grubun 2 açık olayı, olağan ortalama < 1 -> oran 2 -> olasılık oranı √2 ile çarpılır
    assert o.load_ratio == 2.0
    logit = math.log(cond / (1 - cond)) + math.log(math.sqrt(2))
    assert o.probability == pytest.approx(1 / (1 + math.exp(-logit)), abs=1e-4)
    codes = [r.code for r in o.reasons]
    assert set(codes) == {"base_rate", "age", "queue_load"}
    weights = [r.weight for r in o.reasons]
    assert weights == sorted(weights, reverse=True)
    assert any("network_ops kuyruğu olağanın 2.0 katı" in r.message for r in o.reasons)
    # Beklenen çözüm: S(t) <= S(12)/2 ilk t = 20 saat -> kalan 8 saat x √2
    assert o.expected_resolve_at == pytest.approx(
        NOW + 8 * H * math.sqrt(2), abs=timedelta(seconds=1)
    )
    y = _item(rep, young.number)
    assert y.probability < o.probability
    # sıralama: yüksek olasılık önce
    assert rep.items[0].number == old.number


async def test_forecast_falls_back_to_priority_then_none(session):
    await _history(session, group="service_desk_l1")
    await _add(session, *[_inc(created=NOW - 50 * D + i * D, duration=5 * H) for i in range(3)])
    small, unknown, p1 = await _add(
        session,
        _inc(created=NOW - 2 * H, group="network_ops"),  # grupta 3 örnek -> yalnızca öncelik
        _inc(created=NOW - 2 * H, group=None),
        _inc(created=NOW - H, priority=Priority.P1, group="network_ops"),  # P1 geçmişi yok
    )
    rep = await sf.forecast(session, now=NOW)
    s = _item(rep, small.number)
    assert s.basis == "priority" and s.sample_resolved == 23
    assert any(r.code == "fallback_priority" for r in s.reasons)
    assert _item(rep, unknown.number).basis == "priority"
    assert _item(rep, unknown.number).load_ratio is None
    n = _item(rep, p1.number)
    assert n.basis == "none" and n.probability is None and n.expected_resolve_at is None
    assert n.reasons[0].code == "insufficient_sample"
    assert rep.items[-1].number == p1.number  # tahminsizler sonda
    # min_sample düşürülünce grup bazına döner
    rep = await sf.forecast(session, now=NOW, min_sample=3)
    assert _item(rep, small.number).basis == "priority_group"


async def test_forecast_breached_and_paused(session):
    await _history(session)
    breached, paused = await _add(
        session,
        _inc(created=NOW - 30 * H),
        # 10 saat sonra saat durdu (2 saatlik eski duraklatma da var): etkin yaş 8 saat, donuk
        _inc(created=NOW - 3 * D, status=IncidentStatus.ON_HOLD, paused=7200,
             paused_at=NOW - 3 * D + 10 * H),
    )  # fmt: skip
    rep = await sf.forecast(session, now=NOW)
    b = _item(rep, breached.number)
    assert (b.basis, b.probability, b.reasons[0].code) == ("breached", 1.0, "already_breached")
    assert b.remaining_hours == -6.0
    p = _item(rep, paused.number)
    assert p.paused and p.elapsed_hours == 8.0 and p.target_hours == 24.0
    assert "paused" in [r.code for r in p.reasons]
    later = await sf.forecast(session, now=NOW + 5 * D)
    assert _item(later, paused.number).elapsed_hours == 8.0  # saat duruyor


async def test_forecast_timezone_of_now_does_not_matter(session):
    await _history(session)
    await _add(session, _inc(created=NOW - 12 * H))
    utc = await sf.forecast(session, now=NOW)
    ist = await sf.forecast(session, now=NOW.astimezone(ZoneInfo("Europe/Istanbul")))
    naive = await sf.forecast(session, now=NOW.replace(tzinfo=None))
    assert utc.items == ist.items == naive.items


async def test_forecast_filters_and_ignores_other_kinds(session):
    await _history(session)
    await _add(
        session,
        *[_inc(created=NOW - 60 * D + i * D, duration=100 * H, kind=TicketKind.SERVICE_REQUEST)
          for i in range(20)],
    )  # fmt: skip
    a, _b = await _add(
        session, _inc(created=NOW - 12 * H), _inc(created=NOW - 12 * H, group="security_ops")
    )
    rep = await sf.forecast(session, now=NOW, assignment_group="network_ops")
    assert [i.number for i in rep.items] == [a.number]
    # Hizmet talepleri olay örneklemine karışmaz
    assert rep.items[0].sample_resolved == 20
    assert (await sf.forecast(session, now=NOW, min_probability=0.99)).count == 0
    assert (await sf.forecast(session, now=NOW, limit=1)).count == 1


async def test_forecast_is_read_only(session):
    await _history(session)
    await _add(session, _inc(created=NOW - 12 * H))
    await sf.forecast(session, now=NOW)
    await sf.backtest(session, split_at=NOW - 30 * D, end=NOW)
    assert not session.new and not session.dirty and not session.deleted


# --- geriye dönük sınama -------------------------------------------------------------------------


async def test_backtest_time_split_is_calibrated_and_beats_constant_rate(session):
    rng = random.Random(42)
    rows = []
    start = NOW - 200 * D
    for i in range(400):
        created = start + i * 0.45 * D + rng.random() * H
        if i % 2:
            # network_ops: ~%70 ihlal (hedef 24 saat)
            hours = rng.uniform(25, 48) if rng.random() < 0.7 else rng.uniform(1, 23)
            group = "network_ops"
        else:
            # service_desk_l1: ~%10 ihlal
            hours = rng.uniform(25, 48) if rng.random() < 0.1 else rng.uniform(1, 23)
            group = "service_desk_l1"
        paused = 3600 if i % 7 == 0 else 0  # duraklatma bütçeyi bozmaz
        rows.append(_inc(created=created, duration=hours * H, group=group, paused=paused))
    await _add(session, *rows)
    split = NOW - 60 * D
    res = await sf.backtest(session, split_at=split, end=NOW, lookback_days=180)
    assert res.train_size > 200 and res.test_size > 60 and res.skipped == 0
    assert 0.10 < res.baseline_rate < 0.6
    assert res.brier < 0.2 and res.brier < res.baseline_brier
    assert res.skill > 0.15
    assert sum(b.count for b in res.calibration) == res.test_size
    for b in res.calibration:
        if b.count >= 30:
            assert abs(b.mean_predicted - b.observed_rate) < 0.15, b


async def test_backtest_uses_no_future_information(session):
    # Bölmeden önce açılıp sonra çözülen olaylar eğitimde sansürlüdür (gelecekteki süre sızmaz)
    rows = [_inc(created=NOW - 40 * D + i * H, duration=100 * H) for i in range(15)]
    rows += [_inc(created=NOW - 10 * D + i * H, duration=2 * H) for i in range(5)]
    await _add(session, *rows)
    res = await sf.backtest(session, split_at=NOW - 39 * D, end=NOW, lookback_days=60)
    # eğitimde hiç çözülmüş olay yok -> tahmin yapılamaz
    assert res.test_size == 0 and res.brier is None and res.baseline_rate is None
    with pytest.raises(ValueError):
        await sf.backtest(session, split_at=NOW, end=NOW)
