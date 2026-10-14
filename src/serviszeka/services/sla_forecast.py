"""Faz 4 — açık olaylar için açıklanabilir çözüm SLA'sı ihlal tahmini (kara kutu ML yok).

Yöntem (her açık olay için):

1. **Karşılaştırma kümesi:** aynı tür + öncelik + çözüm grubu olayları, son ``lookback_days``
   içinde açılmış (iptal hariç). Çözülmüş olay sayısı ``min_sample``'dan azsa yalnızca tür +
   öncelik kullanılır (``basis=priority``); o da yetmezse tahmin yapılmaz (``basis=none``,
   ``probability=null``).
2. **Etkin süre:** ``çözüm - açılış - sla_paused_seconds`` (arayan/tedarikçi beklemesinde duran
   SLA saati hariç). Hâlâ açık karşılaştırma olayları o ana kadarki etkin yaşlarıyla **sağdan
   sansürlü** gözlem olarak Kaplan–Meier sağkalım eğrisine girer (yalnızca hızlı çözülenleri
   saymanın iyimser yanlılığını önler).
3. **Koşullu ihlal olasılığı:** hedef bütçe ``B = (resolve_due_at - created_at) -
   sla_paused_seconds`` (duraklatma öteleri hariç saf hedef), olayın etkin yaşı ``a`` ise
   ``P(ihlal) = S(B) / S(a)`` (``S(t) = P(T > t)``, KM). Sıfır olasılıkları önlemek için
   ``S'(t) = (n·S(t) + 0.5) / (n + 1)`` düzeltmesi kullanılır; ihlale düşmemiş olayda sonuç
   [0.01, 0.99] aralığına sıkıştırılır. ``a >= B`` ise olay zaten ihlaldedir (``P = 1``).
4. **Kuyruk yükü düzeltmesi:** grubun şu anki açık kayıt sayısı / son ``lookback_days`` içinde
   eşit aralıklı ``LOAD_SNAPSHOTS`` anındaki ortalama açık kayıt sayısı = ``load_ratio``.
   Olasılık oranı ``clamp(load_ratio, 0.5, 3) ** LOAD_ELASTICITY`` ile çarpılır (beklenen kalan
   süre de aynı çarpanla uzar/kısalır).
5. **Beklenen çözüm zamanı:** koşullu medyan — ``S(t) <= S(a)/2`` olan ilk ``t``; ``şimdi + (t -
   a)·çarpan``. Eğri bu yaşın ötesinde yarıya inmiyorsa ``null``. Saat duruyorsa kalan bekleme
   süresi bilinmez; tahmin saatin hemen yeniden işlediğini varsayar.
6. **Gerekçeler:** temel oran, yaş koşullaması, yük ve geri dönüş/duraklatma bilgisi; her biri
   log-olasılık oranına katkısıyla (``weight``) sıralanır.

**Geriye dönük sınama (``backtest``):** zaman tabanlı bölme — ``split_at`` öncesinde açılan olaylar
eğitim (``split_at`` anında hâlâ açık olanlar o andaki yaşla sansürlü; o anki duraklatma bilinmediği
için duvar saati yaşı kullanılır), ``[split_at, end)`` içinde açılan ve sonucu ``end`` anında bilinen
olaylar test: ``end``'den önce çözülenler (gerçek = etkin süre > bütçe) ve ``end``'de hâlâ açık ama
bütçesini çoktan aşmış olanlar (gerçek = ihlal); sonucu henüz bilinmeyen açıklar dışarıda (yalnızca
çözülenleri almak ihlaldeki açık olayları atar: sonuca göre seçim). Tahmin açılış anında (``a = 0``,
yük düzeltmesi yok) yapılır. Brier skoru,
sabit oran (eğitim ihlal oranı) taban çizgisi, beceri skoru ve 5 kovalı kalibrasyon tablosu döner.

Bu modül yalnızca okur (yazma/flush/commit yok).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.domain.enums import IncidentStatus, Priority, TicketKind
from serviszeka.models import Incident
from serviszeka.services.kpi_schemas import (
    BacktestRead,
    BreachForecastRead,
    CalibrationBin,
    ForecastReason,
    ForecastReportRead,
)
from serviszeka.services.sla import CLOSED_STATUSES, ensure_utc

MIN_SAMPLE = 10
LOOKBACK_DAYS = 180
LOAD_SNAPSHOTS = 24
LOAD_ELASTICITY = 0.5
LOAD_CLAMP = (0.5, 3.0)
P_FLOOR, P_CEIL = 0.01, 0.99
_HOUR = 3600.0


# --- Kaplan–Meier --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Survival:
    """KM sağkalım eğrisi: ``S(t) = P(T > t)``; ``steps`` = (olay zamanı, o andan sonraki S)."""

    steps: tuple[tuple[float, float], ...]
    n: int
    events: int

    def at(self, t: float) -> float:
        s = 1.0
        for time, value in self.steps:
            if time > t:
                break
            s = value
        return s

    def smoothed(self, t: float) -> float:
        return (self.n * self.at(t) + 0.5) / (self.n + 1)

    def conditional_median(self, a: float) -> float | None:
        """``S(t) <= S(a)/2`` olan ilk olay zamanı (``t >= a``)."""
        base = self.at(a)
        if base <= 0:
            return None
        for time, value in self.steps:
            if time >= a and value <= base / 2:
                return time
        return None


def kaplan_meier(observations: Iterable[tuple[float, bool]]) -> Survival:
    """observations: (süre saniye, olay mı (çözüldü) — False ise sağdan sansürlü)."""
    obs = sorted((max(float(t), 0.0), bool(e)) for t, e in observations)
    n = len(obs)
    at_risk = n
    s = 1.0
    steps: list[tuple[float, float]] = []
    i = 0
    events = 0
    while i < n:
        t = obs[i][0]
        d = c = 0
        while i < n and obs[i][0] == t:
            if obs[i][1]:
                d += 1
            else:
                c += 1
            i += 1
        if d:
            s *= 1.0 - d / at_risk
            steps.append((t, s))
            events += d
        at_risk -= d + c
    return Survival(tuple(steps), n, events)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# --- veri ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    id: str
    kind: str
    priority: str
    group: str | None
    created: datetime
    resolved: datetime | None
    paused_seconds: int
    paused_at: datetime | None
    open_now: bool
    # Sansürlü gözlemin bilinen son anı (geriye dönük sınamada bölme anı); yoksa `now`
    observed_until: datetime | None = None

    def effective(self, now: datetime) -> tuple[float, bool]:
        """(etkin süre, çözüldü mü)."""
        if self.resolved is not None:
            secs = (self.resolved - self.created).total_seconds() - self.paused_seconds
            return max(secs, 0.0), True
        ref = self.paused_at or self.observed_until or now
        secs = (max(ref, self.created) - self.created).total_seconds() - self.paused_seconds
        return max(secs, 0.0), False

    def open_at(self, t: datetime) -> bool:
        if self.created >= t:
            return False
        if self.resolved is not None:
            return self.resolved > t
        return self.open_now


def _row(inc: Incident) -> _Row:
    return _Row(
        id=inc.id,
        kind=TicketKind(inc.kind).value,
        priority=Priority(inc.priority).value,
        group=inc.assignment_group,
        created=ensure_utc(inc.created_at),
        resolved=ensure_utc(inc.resolved_at) if inc.resolved_at else None,
        paused_seconds=int(inc.sla_paused_seconds or 0),
        paused_at=ensure_utc(inc.sla_paused_at) if inc.sla_paused_at else None,
        open_now=inc.status not in CLOSED_STATUSES,
    )


async def _history(session: AsyncSession, since: datetime, now: datetime) -> list[_Row]:
    """``[since, now]`` aralığında yaşayan (açık olan ya da açılan) iptal edilmemiş olaylar."""
    stmt = select(Incident).where(
        Incident.created_at < now,
        Incident.status != IncidentStatus.CANCELLED,
        or_(Incident.resolved_at.is_(None), Incident.resolved_at >= since),
    )
    return [_as_of(_row(i), now) for i in (await session.scalars(stmt)).all()]


def _as_of(row: _Row, now: datetime) -> _Row:
    """``now`` anındaki görünüm: sonradan çözülen olay o anda açıktı (çözüm süresi sızmaz).

    Geçmiş bir an için tahminde (ör. CSI sinyalleri ``now`` verir) gelecekteki çözüm, olayı
    "çözülmüş" gösterip Kaplan–Meier eğrisine gelecek bilgisini sokardı; sansürlü gözlem olur.
    """
    if row.resolved is None or row.resolved <= now:
        return row
    return _Row(row.id, row.kind, row.priority, row.group, row.created, None,
                row.paused_seconds, None, True)  # fmt: skip


@dataclass
class _Strata:
    by_group: dict[tuple[str, str, str | None], list[_Row]]
    by_priority: dict[tuple[str, str], list[_Row]]


def _strata(rows: Iterable[_Row]) -> _Strata:
    by_group: dict[tuple[str, str, str | None], list[_Row]] = defaultdict(list)
    by_priority: dict[tuple[str, str], list[_Row]] = defaultdict(list)
    for r in rows:
        by_group[(r.kind, r.priority, r.group)].append(r)
        by_priority[(r.kind, r.priority)].append(r)
    return _Strata(by_group, by_priority)


def _sample(
    strata: _Strata, kind: str, priority: str, group: str | None, min_sample: int, exclude: str
) -> tuple[str, list[_Row]]:
    candidates: list[tuple[str, list[_Row]]] = []
    if group is not None:
        candidates.append(("priority_group", strata.by_group.get((kind, priority, group), [])))
    candidates.append(("priority", strata.by_priority.get((kind, priority), [])))
    for basis, rows in candidates:
        rows = [r for r in rows if r.id != exclude]
        if sum(1 for r in rows if r.resolved is not None) >= min_sample:
            return basis, rows
    return "none", []


def _load_ratios(rows: Sequence[_Row], since: datetime, now: datetime) -> dict[str, float]:
    """Grup -> şu anki açık / olağan ortalama açık (eşit aralıklı anlık görüntüler)."""
    span = (now - since) / LOAD_SNAPSHOTS
    points = [since + span * i for i in range(LOAD_SNAPSHOTS)]
    by_group: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        if r.group is not None:
            by_group[r.group].append(r)
    ratios: dict[str, float] = {}
    for group, members in by_group.items():
        mean_open = sum(sum(1 for r in members if r.open_at(t)) for t in points) / len(points)
        current = sum(1 for r in members if r.resolved is None and r.open_now)
        ratios[group] = current / max(mean_open, 1.0)
    return ratios


# --- tahmin --------------------------------------------------------------------------------------


_BASIS_TEXT = {"priority_group": "öncelik + grup", "priority": "yalnızca öncelik"}


def _fmt_hours(seconds: float) -> str:
    return f"{seconds / _HOUR:.1f}"


def forecast_incident(
    inc: _Row,
    due: datetime,
    strata: _Strata,
    now: datetime,
    *,
    min_sample: int = MIN_SAMPLE,
    load_ratio: float | None = None,
) -> dict:
    """Tek olay tahmini (şemaya dönüştürülmeden önce sözlük)."""
    a, _ = inc.effective(now)
    budget = (due - inc.created).total_seconds() - inc.paused_seconds
    paused = inc.paused_at is not None and inc.open_now
    result: dict = {
        "elapsed_hours": round(a / _HOUR, 3),
        "remaining_hours": round((budget - a) / _HOUR, 3),
        "target_hours": round(budget / _HOUR, 3),
        "load_ratio": None if load_ratio is None else round(load_ratio, 3),
        "expected_resolve_at": None,
        "sample_size": 0,
        "sample_resolved": 0,
        "sample_censored": 0,
    }
    reasons: list[ForecastReason] = []
    if paused:
        reasons.append(
            ForecastReason(
                code="paused",
                message="SLA saati durdurulmuş (arayan/tedarikçi bekleniyor); süre işlemiyor.",
                weight=0.0,
            )
        )
    if a >= budget:
        reasons.insert(
            0,
            ForecastReason(
                code="already_breached",
                message=f"Çözüm hedefi ({_fmt_hours(budget)} saat) aşıldı.",
                weight=10.0,
            ),
        )
        return {**result, "basis": "breached", "probability": 1.0, "reasons": reasons}

    basis, rows = _sample(strata, inc.kind, inc.priority, inc.group, min_sample, inc.id)
    if basis == "none":
        reasons.insert(
            0,
            ForecastReason(
                code="insufficient_sample",
                message=f"Benzer çözülmüş olay sayısı {min_sample}'dan az; tahmin yapılmadı.",
                weight=0.0,
            ),
        )
        return {**result, "basis": "none", "probability": None, "reasons": reasons}

    obs = [r.effective(now) for r in rows]
    km = kaplan_meier(obs)
    resolved_n = sum(1 for _, e in obs if e)
    result.update(
        sample_size=len(obs), sample_resolved=resolved_n, sample_censored=len(obs) - resolved_n
    )
    base = min(max(km.smoothed(budget) / km.smoothed(0.0), P_FLOOR), P_CEIL)
    cond = min(max(km.smoothed(budget) / km.smoothed(a), P_FLOOR), P_CEIL)
    label = f"{inc.priority}" + (f" + {inc.group}" if basis == "priority_group" else "")
    reasons.append(
        ForecastReason(
            code="base_rate",
            message=(
                f"Benzer {len(obs)} olayın ({label}) %{base * 100:.0f}'i "
                f"{_fmt_hours(budget)} saatlik çözüm hedefini aşıyor."
            ),
            weight=round(abs(_logit(base)), 3),
        )
    )
    if a > 0:
        reasons.append(
            ForecastReason(
                code="age",
                message=(
                    f"Olay {_fmt_hours(a)} saattir açık (kalan {_fmt_hours(budget - a)} saat); "
                    f"bu yaşa ulaşan benzer olayların %{cond * 100:.0f}'i ihlale düşüyor."
                ),
                weight=round(abs(_logit(cond) - _logit(base)), 3),
            )
        )
    multiplier = 1.0
    if load_ratio is not None:
        multiplier = min(max(load_ratio, LOAD_CLAMP[0]), LOAD_CLAMP[1]) ** LOAD_ELASTICITY
        if not math.isclose(multiplier, 1.0):
            direction = "üzerinde" if load_ratio > 1 else "altında"
            reasons.append(
                ForecastReason(
                    code="queue_load",
                    message=(
                        f"{inc.group} kuyruğu olağanın {load_ratio:.1f} katı ({direction}); "
                        f"risk buna göre ayarlandı."
                    ),
                    weight=round(abs(math.log(multiplier)), 3),
                )
            )
    p = _sigmoid(_logit(cond) + math.log(multiplier))
    p = round(min(max(p, P_FLOOR), P_CEIL), 4)
    if basis == "priority":
        reasons.append(
            ForecastReason(
                code="fallback_priority",
                message=(
                    f"Grup için yeterli örneklem yok (< {min_sample}); "
                    f"{_BASIS_TEXT['priority']} ile karşılaştırıldı."
                ),
                weight=0.0,
            )
        )
    median = km.conditional_median(a)
    if median is not None:
        remaining = (median - a) * multiplier
        result["expected_resolve_at"] = now + timedelta(seconds=remaining)
    reasons.sort(key=lambda r: r.weight, reverse=True)
    return {**result, "basis": basis, "probability": p, "reasons": reasons}


async def forecast(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    min_sample: int = MIN_SAMPLE,
    assignment_group: str | None = None,
    min_probability: float | None = None,
    limit: int = 50,
) -> ForecastReportRead:
    """Açık olayların çözüm SLA'sı ihlal olasılıkları (yüksekten düşüğe; tahminsizler sonda)."""
    now = ensure_utc(now or datetime.now(UTC))
    since = now - timedelta(days=lookback_days)
    rows = await _history(session, since, now)
    sample_rows = [r for r in rows if r.created >= since]
    strata = _strata(sample_rows)
    loads = _load_ratios(rows, since, now)
    stmt = select(Incident).where(
        Incident.status.not_in(list(CLOSED_STATUSES)),
        Incident.resolve_due_at.is_not(None),
        Incident.resolved_at.is_(None),
        Incident.created_at < now,
    )
    if assignment_group is not None:
        stmt = stmt.where(Incident.assignment_group == assignment_group)
    items: list[BreachForecastRead] = []
    for inc in (await session.scalars(stmt)).all():
        row = _row(inc)
        data = forecast_incident(
            row,
            ensure_utc(inc.resolve_due_at),
            strata,
            now,
            min_sample=min_sample,
            load_ratio=loads.get(row.group) if row.group else None,
        )
        if min_probability is not None and (
            data["probability"] is None or data["probability"] < min_probability
        ):
            continue
        items.append(
            BreachForecastRead(
                incident_id=inc.id,
                number=inc.number,
                priority=row.priority,
                assignment_group=row.group,
                status=IncidentStatus(inc.status).value,
                paused=row.paused_at is not None,
                resolve_due_at=inc.resolve_due_at,
                **data,
            )
        )
    items.sort(
        key=lambda i: (
            i.probability is None,
            -(i.probability or 0.0),
            i.resolve_due_at or now,
            i.number,
        )
    )
    items = items[:limit]
    return ForecastReportRead(
        generated_at=now,
        lookback_days=lookback_days,
        min_sample=min_sample,
        count=len(items),
        items=items,
    )


# --- geriye dönük sınama -------------------------------------------------------------------------


def brier(pairs: Sequence[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def calibration(pairs: Sequence[tuple[float, int]], bins: int = 5) -> list[CalibrationBin]:
    out: list[CalibrationBin] = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        members = [(p, y) for p, y in pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        out.append(
            CalibrationBin(
                lower=lo,
                upper=hi,
                count=len(members),
                mean_predicted=round(sum(p for p, _ in members) / len(members), 4)
                if members
                else None,
                observed_rate=round(sum(y for _, y in members) / len(members), 4)
                if members
                else None,
            )
        )
    return out


def _known_outcome(r: _Row, budget: float, end: datetime) -> int | None:
    """Test olayının ``end`` anında bilinen sonucu: 1 ihlal, 0 hedefinde, None henüz bilinmiyor.

    Çözümü ``end``'den önceyse etkin süre bütçeyle karşılaştırılır. Bitişte hâlâ açık (ya da
    sonradan çözülen) olayın bitişteki etkin yaşı bütçeyi aştıysa ihlal kesindir; aşmadıysa sonuç
    bilinmez ve test dışında kalır. Yalnızca çözülenleri saymak, en uzun süren (ihlaldeki) açık
    olayları atarak gerçek ihlal oranını olduğundan düşük gösterirdi (sonuca göre seçim).
    """
    if r.resolved is not None and r.resolved < end:
        return 1 if r.effective(end)[0] > budget else 0
    if r.resolved is None and not r.open_now:
        return None  # çözülmeden kapatılmış/iptal: sonuç tanımsız
    ref = min(r.paused_at, end) if r.paused_at is not None else end
    age = (max(ref, r.created) - r.created).total_seconds() - r.paused_seconds
    return 1 if age > budget else None


async def backtest(
    session: AsyncSession,
    *,
    split_at: datetime,
    end: datetime | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    min_sample: int = MIN_SAMPLE,
) -> BacktestRead:
    """Zaman tabanlı bölmeyle kalibrasyon (Brier) — bkz. modül belgesi."""
    split_at = ensure_utc(split_at)
    end = ensure_utc(end or datetime.now(UTC))
    if end <= split_at:
        raise ValueError("Bitiş, bölme anından sonra olmalı.")
    try:
        since = split_at - timedelta(days=lookback_days)
    except OverflowError as exc:  # API 422, 500 değil
        raise ValueError("Bölme anı desteklenen aralığın dışında.") from exc
    stmt = select(Incident).where(
        Incident.created_at >= since,
        Incident.created_at < end,
        Incident.status != IncidentStatus.CANCELLED,
    )
    incidents = list((await session.scalars(stmt)).all())

    train: list[_Row] = []
    for inc in incidents:
        r = _row(inc)
        if r.created >= split_at:
            continue
        if r.resolved is not None and r.resolved <= split_at:
            train.append(r)
        else:
            # Bölme anında açık: o andaki duvar saati yaşıyla sansürlü (gelecek bilgisi yok)
            train.append(
                _Row(r.id, r.kind, r.priority, r.group, r.created, None, 0, None, True, split_at)
            )
    strata = _strata(train)
    train_resolved = [r for r in train if r.resolved is not None]
    train_breach = []
    budgets = {inc.id: inc for inc in incidents}
    for r in train_resolved:
        inc = budgets[r.id]
        if inc.resolve_due_at is None:
            continue
        budget = (ensure_utc(inc.resolve_due_at) - r.created).total_seconds() - r.paused_seconds
        train_breach.append(1 if r.effective(split_at)[0] > budget else 0)
    baseline_rate = sum(train_breach) / len(train_breach) if train_breach else None

    pairs: list[tuple[float, int]] = []
    skipped = 0
    for inc in incidents:
        r = _row(inc)
        if r.created < split_at or inc.resolve_due_at is None:
            continue
        due = ensure_utc(inc.resolve_due_at)
        budget = (due - r.created).total_seconds() - r.paused_seconds
        actual = _known_outcome(r, budget, end)
        if actual is None:
            continue
        # Açılış anındaki görünüm: saf hedef bütçe (sonraki duraklatma ötelemesi hariç)
        fresh = _Row(r.id, r.kind, r.priority, r.group, r.created, None, 0, None, True)
        pure_due = due - timedelta(seconds=r.paused_seconds)
        data = forecast_incident(fresh, pure_due, strata, r.created, min_sample=min_sample)
        if data["probability"] is None:
            skipped += 1
            continue
        pairs.append((float(data["probability"]), actual))

    score = brier(pairs)
    base_score = (
        brier([(baseline_rate, y) for _, y in pairs])
        if baseline_rate is not None and pairs
        else None
    )
    skill = (
        round(1 - score / base_score, 4)
        if score is not None and base_score not in (None, 0)
        else None
    )
    return BacktestRead(
        split_at=split_at,
        end=end,
        lookback_days=lookback_days,
        train_size=len(train),
        test_size=len(pairs),
        skipped=skipped,
        brier=None if score is None else round(score, 4),
        baseline_brier=None if base_score is None else round(base_score, 4),
        baseline_rate=None if baseline_rate is None else round(baseline_rate, 4),
        skill=skill,
        calibration=calibration(pairs),
    )
