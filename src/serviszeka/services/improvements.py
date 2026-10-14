"""Sürekli iyileştirme (ITIL 4 Continual Improvement) kaydı servisi.

Durum makinesi::

    idea ─► approved ─► in_progress ─► measured ─► closed
     │          │            │   ▲         │
     └──────────┴────────────┴─► rejected  └─ (yeniden ölçüm: measured ─► measured)

- **Onay, red, başlatma, uygulandı işareti ve kapatma yalnızca insandır** (`ai:` önekli ya da
  `ai_agent` rollü aktör `ImprovementForbiddenError`). Yapay zekâ (`ai:csi`) yalnızca fikir açar
  (`csi.create_improvement`) ve ölçer (`csi.measure_improvement`). Rol kuralları (onay/red/kapatma:
  approver/admin) API katmanındadır.
- **Ölçüm:** bağlı KPI (`kpi_key`, isteğe bağlı kırılım) taban değeri kayıtta
  `[şimdi - measure_days, şimdi)` penceresinden hesaplanır (verilmediyse). Uygulama bitince
  (`implemented_at`) `[implemented_at, implemented_at + measure_days)` penceresi dolduğunda ölçülür:
  `measured_value`, `target_met` (hedef varsa KPI'nın iyi yönüne göre hedefe ulaşıldı mı, yoksa
  tabana göre iyileşti mi) ve `measurement` (önce/sonra, fark, % fark). KPI'ya bağlı kayıt
  ölçülmeden kapatılamaz; KPI'sız kayıt uygulamadan notla kapatılır.
- KPI tanımı, taban ve hedef yalnızca onaydan önce (`idea`) değişir: ölçüm sonradan oyunlanamaz.
- Her yazma sürümü artırır (`expected_version` -> `ImprovementVersionConflictError`) ve
  `audit.record` ile denetime yazılır. Fonksiyonlar flush eder, commit etmez.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import ImprovementSource, ImprovementState
from serviszeka.models import ChangeRequest, Improvement, Problem
from serviszeka.services import kpi
from serviszeka.services.improvement_schemas import (
    ImprovementCreate,
    ImprovementUpdate,
    check_kpi_scope,
)
from serviszeka.services.sla import ensure_utc

IS = ImprovementState
NUMBER_PREFIX = "CSI"
_NUMBER_RE = re.compile(r"^CSI(\d+)$")
_AI_PREFIX = "ai:"

OPEN_STATES: frozenset[ImprovementState] = frozenset(
    {IS.IDEA, IS.APPROVED, IS.IN_PROGRESS, IS.MEASURED}
)
REJECTABLE: frozenset[ImprovementState] = frozenset({IS.IDEA, IS.APPROVED, IS.IN_PROGRESS})
# KPI tanımı / taban / hedef yalnızca bu durumda değişir
KPI_FIELDS: frozenset[str] = frozenset(
    {"kpi_key", "kpi_dimension", "kpi_dimension_value", "baseline_value", "target_value",
     "measure_days"}
)  # fmt: skip


class ImprovementError(Exception):
    pass


class ImprovementNotFoundError(ImprovementError, LookupError):
    pass


class ImprovementValidationError(ImprovementError, ValueError):
    pass


class InvalidImprovementTransitionError(ImprovementError, ValueError):
    pass


class ImprovementForbiddenError(ImprovementError, PermissionError):
    pass


class ImprovementNotReadyError(ImprovementError):
    """Ölçüm penceresi henüz dolmadı."""


class ImprovementVersionConflictError(ImprovementError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"İyileştirme kaydı başka biri tarafından güncellendi (beklenen sürüm {expected}, "
            f"güncel {actual}); yeniden yükleyin."
        )
        self.expected = expected
        self.actual = actual


def _utc(now: datetime | None) -> datetime:
    return ensure_utc(now) if now else datetime.now(UTC)


def is_ai_actor(user: CurrentUser) -> bool:
    return user.username.startswith(_AI_PREFIX) or "ai_agent" in user.roles


def _human_only(user: CurrentUser, what: str) -> None:
    if is_ai_actor(user):
        raise ImprovementForbiddenError(f"Yapay zekâ aktörü iyileştirmeyi {what}; insan kararı gerekir.")


def priority_score(benefit: int, effort: int) -> float:
    """Fayda / efor * 20 (fayda ve efor 1-5 -> 4..100)."""
    return round(benefit / max(effort, 1) * 20, 1)


async def next_number(session: AsyncSession) -> str:
    numbers = (
        await session.scalars(
            select(Improvement.number).where(Improvement.number.like(f"{NUMBER_PREFIX}%"))
        )
    ).all()
    highest = max((int(m.group(1)) for n in numbers if (m := _NUMBER_RE.match(n or ""))), default=0)
    return f"{NUMBER_PREFIX}{highest + 1:07d}"


# --- KPI ölçümü ---------------------------------------------------------------------------------


async def metric_value(
    session: AsyncSession,
    kpi_key: str,
    window: kpi.Window,
    *,
    dimension: str | None = None,
    dimension_value: str | None = None,
    now: datetime | None = None,
) -> tuple[float | None, int]:
    """KPI'nın pencere değeri ve örneklemi; kırılım verilirse o kırılım satırı (yoksa (None, 0))."""
    result = await kpi.compute(session, kpi_key, window, now)
    if dimension is None:
        return result.value, result.count
    for stat in result.breakdown.get(dimension, []):
        if stat.key == dimension_value:
            return stat.value, stat.count
    return None, 0


def _window_dict(w: kpi.Window | None) -> dict[str, str] | None:
    return None if w is None else {"start": w.start.isoformat(), "end": w.end.isoformat()}


def target_reached(better: str, value: float, target: float) -> bool:
    return value <= target if better == "lower" else value >= target


async def _compute_baseline(session: AsyncSession, imp: Improvement, now: datetime) -> None:
    window = kpi.Window(now - timedelta(days=imp.measure_days), now)
    value, _ = await metric_value(
        session, imp.kpi_key, window, dimension=imp.kpi_dimension,
        dimension_value=imp.kpi_dimension_value, now=now,
    )  # fmt: skip
    imp.baseline_value = value
    imp.baseline_start, imp.baseline_end = window.start, window.end


# --- okuma ---------------------------------------------------------------------------------------


async def get_improvement(session: AsyncSession, improvement_id: str) -> Improvement | None:
    return await session.get(Improvement, improvement_id)


async def _require(
    session: AsyncSession, improvement_id: str, expected_version: int | None = None
) -> Improvement:
    imp = await session.get(Improvement, improvement_id)
    if imp is None:
        raise ImprovementNotFoundError(f"İyileştirme bulunamadı: {improvement_id}")
    db_version = await session.scalar(
        select(Improvement.version).where(Improvement.id == improvement_id).with_for_update()
    )
    if db_version != imp.version:
        await session.refresh(imp)
    if expected_version is not None and expected_version != imp.version:
        raise ImprovementVersionConflictError(expected_version, imp.version)
    return imp


async def list_improvements(
    session: AsyncSession,
    *,
    state: ImprovementState | str | None = None,
    source: ImprovementSource | str | None = None,
    kpi_key: str | None = None,
    owner: str | None = None,
    q: str | None = None,
    open_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Improvement]:
    stmt = select(Improvement)
    if state is not None:
        stmt = stmt.where(Improvement.state == ImprovementState(state))
    if source is not None:
        stmt = stmt.where(Improvement.source == ImprovementSource(source))
    if kpi_key:
        stmt = stmt.where(Improvement.kpi_key == kpi_key)
    if owner:
        stmt = stmt.where(Improvement.owner == owner)
    if open_only:
        stmt = stmt.where(Improvement.state.in_(list(OPEN_STATES)))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(func.lower(Improvement.number).like(like), func.lower(Improvement.title).like(like))
        )
    stmt = stmt.order_by(Improvement.created_at.desc(), Improvement.number.desc())
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


async def signal_blocked(
    session: AsyncSession, signal_key: str, *, now: datetime, cooldown_days: int
) -> bool:
    """Sinyal için açık kayıt var mı ya da bekleme süresi içinde kapanan/reddedilen kayıt var mı."""
    since = now - timedelta(days=cooldown_days)
    rows = (
        await session.scalars(select(Improvement).where(Improvement.signal_key == signal_key))
    ).all()
    for imp in rows:
        if ImprovementState(imp.state) in OPEN_STATES:
            return True
        if imp.updated_at is not None and ensure_utc(imp.updated_at) >= since:
            return True
    return False


async def due_for_measurement(session: AsyncSession, now: datetime) -> list[Improvement]:
    """Uygulaması bitmiş ve ölçüm penceresi dolmuş, henüz ölçülmemiş KPI'lı kayıtlar."""
    rows = (
        await session.scalars(
            select(Improvement)
            .where(
                Improvement.state == IS.IN_PROGRESS,
                Improvement.kpi_key.is_not(None),
                Improvement.implemented_at.is_not(None),
            )
            .order_by(Improvement.number)
        )
    ).all()
    return [
        imp
        for imp in rows
        if ensure_utc(imp.implemented_at) + timedelta(days=imp.measure_days) <= now
    ]


# --- yazma ---------------------------------------------------------------------------------------


async def _touch(
    session: AsyncSession,
    imp: Improvement,
    user: CurrentUser,
    action: str,
    details: dict[str, Any],
    now: datetime,
) -> Improvement:
    imp.version = (imp.version or 0) + 1
    imp.updated_at = now
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action=action,
        entity_type="improvement",
        entity_id=imp.id,
        details={"number": imp.number, "version": imp.version, **details},
    )
    return imp


async def _check_links(session: AsyncSession, problem_id: str | None, change_id: str | None) -> None:
    if problem_id and await session.get(Problem, problem_id) is None:
        raise ImprovementValidationError(f"Sorun bulunamadı: {problem_id}")
    if change_id and await session.get(ChangeRequest, change_id) is None:
        raise ImprovementValidationError(f"Değişiklik bulunamadı: {change_id}")


async def create_improvement(
    session: AsyncSession,
    data: ImprovementCreate,
    user: CurrentUser,
    *,
    detected_by: str | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> Improvement:
    """Fikir (idea) olarak açar; KPI'ya bağlıysa ve taban verilmediyse tabanı hesaplar."""
    settings = settings or get_settings()
    now = _utc(now)
    await _check_links(session, data.problem_id, data.change_id)
    imp = Improvement(
        number=await next_number(session),
        title=data.title,
        description=data.description,
        source=data.source,
        state=IS.IDEA,
        kpi_key=data.kpi_key,
        kpi_dimension=data.kpi_dimension,
        kpi_dimension_value=data.kpi_dimension_value,
        baseline_value=data.baseline_value,
        target_value=data.target_value,
        measure_days=data.measure_days or settings.csi_measure_days,
        owner=data.owner,
        benefit_estimate=data.benefit_estimate,
        effort=data.effort,
        priority_score=priority_score(data.benefit_estimate, data.effort),
        problem_id=data.problem_id,
        change_id=data.change_id,
        evidence=data.evidence,
        signal_key=data.signal_key,
        detected_by=detected_by or user.username,
        created_by=user.username,
        version=1,
        created_at=now,
        updated_at=now,
    )
    if imp.kpi_key and imp.baseline_value is None:
        await _compute_baseline(session, imp, now)
    session.add(imp)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="improvement.create",
        entity_type="improvement",
        entity_id=imp.id,
        details={
            "number": imp.number,
            "source": ImprovementSource(imp.source).value,
            "detected_by": imp.detected_by,
            "kpi_key": imp.kpi_key,
            "baseline_value": imp.baseline_value,
            "target_value": imp.target_value,
            "signal_key": imp.signal_key,
        },
    )
    return imp


def _check_open(imp: Improvement) -> None:
    if ImprovementState(imp.state) not in OPEN_STATES:
        raise ImprovementValidationError(
            f"{imp.number} {ImprovementState(imp.state).value}; değiştirilemez."
        )


async def update_improvement(
    session: AsyncSession,
    improvement_id: str,
    data: ImprovementUpdate,
    user: CurrentUser,
    *,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> Improvement:
    now = _utc(now)
    values = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    if expected_version is None:
        expected_version = data.expected_version
    imp = await _require(session, improvement_id, expected_version)
    _check_open(imp)
    if set(values) & KPI_FIELDS and ImprovementState(imp.state) != IS.IDEA:
        raise ImprovementValidationError(
            "KPI, taban, hedef ve ölçüm penceresi yalnızca onaydan önce değiştirilebilir."
        )
    changed: dict[str, Any] = {}
    for key, value in values.items():
        if key in ("title", "benefit_estimate", "effort", "measure_days") and value is None:
            continue
        if key == "description" and value is None:
            value = ""
        if getattr(imp, key) != value:
            setattr(imp, key, value)
            changed[key] = value
    if not changed:
        return imp
    try:
        check_kpi_scope(imp.kpi_key, imp.kpi_dimension, imp.kpi_dimension_value)
    except ValueError as exc:
        raise ImprovementValidationError(str(exc)) from exc
    imp.priority_score = priority_score(imp.benefit_estimate, imp.effort)
    if set(changed) & (KPI_FIELDS - {"baseline_value", "target_value"}) and (
        "baseline_value" not in values
    ):
        imp.baseline_value = imp.baseline_start = imp.baseline_end = None
        if imp.kpi_key:
            await _compute_baseline(session, imp, now)
    return await _touch(
        session, imp, user, "improvement.update", {"fields": sorted(changed)}, now
    )


async def _move(
    session: AsyncSession,
    improvement_id: str,
    user: CurrentUser,
    allowed_from: frozenset[ImprovementState],
    target: ImprovementState,
    expected_version: int | None,
) -> tuple[Improvement, ImprovementState]:
    imp = await _require(session, improvement_id, expected_version)
    current = ImprovementState(imp.state)
    if current not in allowed_from:
        raise InvalidImprovementTransitionError(
            f"{current.value} -> {target.value} geçişine izin verilmez."
        )
    return imp, current


async def approve(
    session: AsyncSession,
    improvement_id: str,
    user: CurrentUser,
    *,
    note: str | None = None,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> Improvement:
    _human_only(user, "onaylayamaz")
    now = _utc(now)
    imp, current = await _move(
        session, improvement_id, user, frozenset({IS.IDEA}), IS.APPROVED, expected_version
    )
    imp.state, imp.approved_by, imp.approved_at = IS.APPROVED, user.username, now
    return await _touch(
        session, imp, user, "improvement.approve",
        {"from": current.value, "to": IS.APPROVED.value, "note": (note or "").strip() or None}, now,
    )  # fmt: skip


async def reject(
    session: AsyncSession,
    improvement_id: str,
    user: CurrentUser,
    *,
    note: str | None,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> Improvement:
    _human_only(user, "reddedemez")
    now = _utc(now)
    text = (note or "").strip()
    if not text:
        raise ImprovementValidationError("Red için gerekçe (not) zorunludur.")
    imp, current = await _move(
        session, improvement_id, user, REJECTABLE, IS.REJECTED, expected_version
    )
    imp.state, imp.resolution = IS.REJECTED, text
    imp.closed_by, imp.closed_at = user.username, now
    return await _touch(
        session, imp, user, "improvement.reject",
        {"from": current.value, "to": IS.REJECTED.value, "note": text}, now,
    )  # fmt: skip


async def start(
    session: AsyncSession,
    improvement_id: str,
    user: CurrentUser,
    *,
    note: str | None = None,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> Improvement:
    _human_only(user, "başlatamaz")
    now = _utc(now)
    imp, current = await _move(
        session, improvement_id, user, frozenset({IS.APPROVED}), IS.IN_PROGRESS, expected_version
    )
    imp.state, imp.started_at = IS.IN_PROGRESS, now
    return await _touch(
        session, imp, user, "improvement.start",
        {"from": current.value, "to": IS.IN_PROGRESS.value, "note": (note or "").strip() or None},
        now,
    )  # fmt: skip


async def mark_implemented(
    session: AsyncSession,
    improvement_id: str,
    user: CurrentUser,
    *,
    at: datetime | None = None,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> Improvement:
    """Uygulamanın bittiği anı kaydeder; ölçüm penceresi buradan başlar."""
    _human_only(user, "uygulandı olarak işaretleyemez")
    now = _utc(now)
    at = ensure_utc(at) if at else now
    imp = await _require(session, improvement_id, expected_version)
    if ImprovementState(imp.state) != IS.IN_PROGRESS:
        raise InvalidImprovementTransitionError("Yalnızca uygulamadaki iyileştirme işaretlenir.")
    if at > now:
        raise ImprovementValidationError("Uygulama zamanı gelecekte olamaz.")
    if imp.started_at is not None and at < ensure_utc(imp.started_at):
        raise ImprovementValidationError("Uygulama zamanı başlangıçtan önce olamaz.")
    imp.implemented_at = at
    return await _touch(
        session, imp, user, "improvement.implemented", {"implemented_at": at.isoformat()}, now
    )


async def measure(
    session: AsyncSession,
    improvement_id: str,
    user: CurrentUser,
    *,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> Improvement:
    """Önce/sonra KPI karşılaştırması (yapay zekâ da çağırabilir: deterministik hesap)."""
    now = _utc(now)
    imp = await _require(session, improvement_id, expected_version)
    current = ImprovementState(imp.state)
    if current not in (IS.IN_PROGRESS, IS.MEASURED):
        raise InvalidImprovementTransitionError(
            f"{current.value} durumundaki iyileştirme ölçülemez."
        )
    if not imp.kpi_key:
        raise ImprovementValidationError("KPI'ya bağlı olmayan iyileştirme ölçülemez.")
    if imp.implemented_at is None:
        raise InvalidImprovementTransitionError("Önce uygulamanın bittiği an işaretlenmelidir.")
    start_at = ensure_utc(imp.implemented_at)
    after = kpi.Window(start_at, start_at + timedelta(days=imp.measure_days))
    if after.end > now:
        raise ImprovementNotReadyError(
            f"Ölçüm penceresi henüz dolmadı ({after.end.isoformat()} sonrasında ölçülebilir)."
        )
    value, count = await metric_value(
        session, imp.kpi_key, after, dimension=imp.kpi_dimension,
        dimension_value=imp.kpi_dimension_value, now=now,
    )  # fmt: skip
    if value is None:
        raise ImprovementValidationError("Ölçüm penceresinde KPI verisi yok.")
    defn = kpi.DEFINITIONS[imp.kpi_key]
    before = imp.baseline_value
    delta = delta_pct = improved = None
    if before is not None:
        delta = round(value - before, 3)
        if before != 0:
            delta_pct = round(100.0 * (value - before) / abs(before), 2)
        improved = kpi.trend(defn.better, value, before) == "improved"
    if imp.target_value is not None:
        target_met: bool | None = target_reached(defn.better, value, imp.target_value)
    else:
        target_met = improved
    baseline_window = (
        kpi.Window(imp.baseline_start, imp.baseline_end)
        if imp.baseline_start and imp.baseline_end
        else None
    )
    imp.measured_value = value
    imp.measured_at = now
    imp.target_met = target_met
    imp.measurement = {
        "kpi_key": imp.kpi_key,
        "name": defn.name,
        "unit": defn.unit,
        "better": defn.better,
        "dimension": (
            {"name": imp.kpi_dimension, "value": imp.kpi_dimension_value}
            if imp.kpi_dimension
            else None
        ),
        "before": {"value": before, "window": _window_dict(baseline_window)},
        "after": {"value": value, "count": count, "window": _window_dict(after)},
        "target_value": imp.target_value,
        "delta": delta,
        "delta_pct": delta_pct,
        "improved": improved,
        "target_met": target_met,
        "measured_at": now.isoformat(),
    }
    imp.state = IS.MEASURED
    return await _touch(
        session, imp, user, "improvement.measure",
        {"from": current.value, "to": IS.MEASURED.value, "measured_value": value,
         "baseline_value": before, "delta_pct": delta_pct, "target_met": target_met},
        now,
    )  # fmt: skip


async def close(
    session: AsyncSession,
    improvement_id: str,
    user: CurrentUser,
    *,
    note: str | None = None,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> Improvement:
    """Ölçülen iyileştirmeyi kapatır; KPI'sız iyileştirme uygulamadan notla kapanır."""
    _human_only(user, "kapatamaz")
    now = _utc(now)
    imp = await _require(session, improvement_id, expected_version)
    current = ImprovementState(imp.state)
    text = (note or "").strip() or None
    if current == IS.IN_PROGRESS and not imp.kpi_key:
        if not text:
            raise ImprovementValidationError("KPI'sız iyileştirme kapanış notuyla kapatılır.")
    elif current != IS.MEASURED:
        raise InvalidImprovementTransitionError(
            f"{current.value} -> closed geçişine izin verilmez (KPI'lı iyileştirme önce ölçülür)."
        )
    imp.state, imp.closed_by, imp.closed_at = IS.CLOSED, user.username, now
    if text:
        imp.resolution = text
    return await _touch(
        session, imp, user, "improvement.close",
        {"from": current.value, "to": IS.CLOSED.value, "note": text,
         "target_met": imp.target_met},
        now,
    )  # fmt: skip
