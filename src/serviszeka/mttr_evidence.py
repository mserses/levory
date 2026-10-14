"""Faz 4 MTTR kanıtı — **SENTETİK** önce/sonra senaryosu (`evals/run_mttr_evidence.py`).

Amaç: "MTTR düşüşü veriden hesaplanabilir" iddiasının uçtan uca sınaması. Gerçek veri yoktur;
olaylar deterministik olarak üretilir ve doğrudan tabloya yazılır (denetim zinciri, ajan, LLM yok).

Senaryo (`now` = pencere sonu, varsayılan şimdi):

- Hedef grup (`network_ops`): önceki pencerede `[now - 2d, now - d)` ortalama etkin çözüm süresi
  `before_hours` (bir kısmı arayan beklemesiyle duraklatılmış SLA saati içerir), iyileştirme
  `now - d` anında uygulanır, sonraki pencerede `[now - d, now)` ortalama `after_hours`.
- Kontrol grubu (`dba`): iki pencerede de aynı ortalama (değişmemeli).
- Sürekli iyileştirme kaydı (`services/improvements.py`): `now - d` anında MTTR'ye (grup kırılımı)
  bağlı iyileştirme açılır (taban önceki pencereden hesaplanır), onaylanır, başlatılır, uygulandı
  işaretlenir; `now` anında önce/sonra ölçülür ve kapatılır.

Kapı (`gate.passed`): KPI raporu (`kpi.report`, önceki eşit pencereyle) bağımsız aritmetikle
(`reference_mean_hours`: duvar saati − duraklatma) aynı (±0,01 sa), hedef grupta düşüş var,
kontrol grubu sabit (|Δ| < %10), CSI ölçümü KPI raporuyla aynı `delta_pct`'yi verir ve hedefe ulaşır.

Aynı üreteç (`cohort` + `seed`) E2E tohumlamasında da kullanılır (`portal/e2e/faz4_data.py`).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from serviszeka.auth import CurrentUser
from serviszeka.db import Base
from serviszeka.domain.enums import IncidentStatus, Priority, TicketKind
from serviszeka.models import Incident
from serviszeka.services import improvements, kpi
from serviszeka.services import incidents as incident_service
from serviszeka.services.improvement_schemas import ImprovementCreate, ImprovementUpdate

TARGET_GROUP = "network_ops"
CONTROL_GROUP = "dba"
# Ortalama etrafında sabit, toplamı sıfır sapma deseni (deterministik "gürültü")
_SPREAD = (-0.3, 0.2, -0.1, 0.4, -0.2, 0.1, 0.3, -0.4)


@dataclass(frozen=True)
class IncidentSpec:
    """Üretilecek tek çözülmüş olay (SENTETİK)."""

    group: str
    category: str
    service: str
    priority: Priority
    created_at: datetime
    resolved_at: datetime
    paused_hours: float
    effective_hours: float
    start: datetime  # üretildiği pencere (yalnızca doğrulama için)
    end: datetime


def cohort(
    *,
    group: str,
    count: int,
    mean_hours: float,
    start: datetime,
    end: datetime,
    paused_hours: float = 0.0,
    category: str = "ag",
    service: str = "vpn",
    priorities: tuple[Priority, ...] = (Priority.P3, Priority.P4, Priority.P2),
) -> list[IncidentSpec]:
    """`[start, end)` içinde eşit aralıklarla çözülen `count` olay; etkin süre ortalaması tam olarak
    `mean_hours` (sapmalar ortalamadan çıkarılır). Her ikinci olay `paused_hours` duraklatılmıştır."""
    if count < 1 or end <= start:
        raise ValueError("count >= 1 ve end > start olmalı.")
    spreads = [_SPREAD[i % len(_SPREAD)] for i in range(count)]
    shift = statistics.fmean(spreads)
    step = (end - start) / count
    out = []
    for i in range(count):
        effective = round(mean_hours * (1.0 + spreads[i] - shift), 6)
        paused = paused_hours if i % 2 else 0.0
        resolved = start + step * i + step / 2
        created = resolved - timedelta(hours=effective + paused)
        out.append(
            IncidentSpec(
                group=group, category=category, service=service,
                priority=priorities[i % len(priorities)], created_at=created,
                resolved_at=resolved, paused_hours=paused, effective_hours=effective,
                start=start, end=end,
            )
        )  # fmt: skip
    return out


def reference_mean_hours(specs: list[IncidentSpec]) -> float:
    """Bağımsız aritmetik: ort(resolved_at − created_at − duraklatma), saat."""
    return statistics.fmean(
        (s.resolved_at - s.created_at).total_seconds() / 3600 - s.paused_hours for s in specs
    )


async def seed(session: AsyncSession, specs: list[IncidentSpec]) -> list[Incident]:
    """Olayları doğrudan ekler (commit etmez): sıradaki `INC` numaraları, arayan `sentetik`,
    SLA vadeleri öncelikten (`incidents.apply_sla`, duraklatma vadeye eklenir)."""
    base = int((await incident_service.next_number(session))[3:])
    rows = []
    for i, s in enumerate(specs):
        incident = Incident(
            number=f"INC{base + i:07d}",
            kind=TicketKind.INCIDENT,
            title=f"[Sentetik] {s.group} olayı {base + i}",
            description="Sentetik MTTR kanıt senaryosu (gerçek veri değil).",
            caller="sentetik",
            category=s.category,
            service=s.service,
            priority=s.priority,
            status=IncidentStatus.RESOLVED,
            assignment_group=s.group,
            resolution="Sentetik çözüm.",
            responded_at=s.created_at + timedelta(minutes=20),
            resolved_at=s.resolved_at,
            sla_paused_seconds=round(s.paused_hours * 3600),
            created_at=s.created_at,
            updated_at=s.resolved_at,
        )
        incident_service.apply_sla(incident)
        session.add(incident)
        rows.append(incident)
    await session.flush()
    return rows


# --- kanıt raporu ----------------------------------------------------------------------------


@dataclass
class EvidenceReport:
    now: datetime
    days: int
    before_hours: float
    after_hours: float
    reference: dict[str, float]
    kpi_overall: dict[str, Any]
    kpi_group: dict[str, Any]
    kpi_control: dict[str, Any]
    improvement: dict[str, Any]
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "synthetic": True,
            "now": self.now.isoformat(),
            "window_days": self.days,
            "target_group": TARGET_GROUP,
            "control_group": CONTROL_GROUP,
            "reference": self.reference,
            "kpi": {"overall": self.kpi_overall, "group": self.kpi_group,
                    "control": self.kpi_control},
            "improvement": self.improvement,
            "gate": {"checks": self.checks, "passed": self.passed},
        }  # fmt: skip


def _stat(report: Any, group: str) -> dict[str, Any]:
    [value] = report.kpis
    cur = {g.key: g for g in value.breakdown.get("assignment_group", [])}
    return {"value": cur[group].value if group in cur else None, "count": cur[group].count
            if group in cur else 0}  # fmt: skip


def _pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return round(100.0 * (cur - prev) / abs(prev), 2)  # type: ignore[operator]


async def _group_values(
    session: AsyncSession, group: str, now: datetime, days: int
) -> dict[str, Any]:
    w = kpi.Window.ending(now, days)
    cur = _stat(await kpi.report(session, end=w.end, days=days, keys=["mttr"], now=now), group)
    prev = _stat(await kpi.report(session, end=w.start, days=days, keys=["mttr"], now=now), group)
    return {
        "value": cur["value"], "count": cur["count"], "previous": prev["value"],
        "previous_count": prev["count"], "delta_pct": _pct(cur["value"], prev["value"]),
    }  # fmt: skip


async def run(
    *,
    now: datetime | None = None,
    days: int = 30,
    before_hours: float = 9.5,
    after_hours: float = 4.25,
    control_hours: float = 6.0,
    per_window: int = 24,
) -> EvidenceReport:
    """Bellek içi SQLite'ta senaryoyu kurar, KPI ve CSI ölçümünü hesaplar (ağ ve LLM yok)."""
    now = kpi.to_utc(now or datetime.now(UTC)).replace(microsecond=0)
    d = timedelta(days=days)
    implemented = now - d
    before = cohort(group=TARGET_GROUP, count=per_window, mean_hours=before_hours,
                    start=now - 2 * d, end=implemented, paused_hours=1.5)  # fmt: skip
    after = cohort(group=TARGET_GROUP, count=per_window, mean_hours=after_hours,
                   start=implemented, end=now, paused_hours=0.5)  # fmt: skip
    control = [
        *cohort(group=CONTROL_GROUP, count=per_window // 2, mean_hours=control_hours,
                start=now - 2 * d, end=implemented, category="veritabani",
                service="veri_platformu"),
        *cohort(group=CONTROL_GROUP, count=per_window // 2, mean_hours=control_hours,
                start=implemented, end=now, category="veritabani", service="veri_platformu"),
    ]  # fmt: skip

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            from serviszeka import models  # noqa: F401  (tabloları kaydeder)

            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await seed(session, before + after + control)
            await session.commit()

            overall = await kpi.report(session, end=now, days=days, keys=["mttr"], now=now)
            [mttr] = overall.kpis
            group = await _group_values(session, TARGET_GROUP, now, days)
            ctrl = await _group_values(session, CONTROL_GROUP, now, days)
            imp = await _improvement_lifecycle(session, now=now, implemented=implemented, days=days)
            await session.commit()
    finally:
        await engine.dispose()

    reference = {
        "before_hours": round(reference_mean_hours(before), 3),
        "after_hours": round(reference_mean_hours(after), 3),
        "control_hours": round(reference_mean_hours(control), 3),
    }
    reference["delta_pct"] = _pct(reference["after_hours"], reference["before_hours"])  # type: ignore[assignment]
    measurement = imp["measurement"] or {}
    checks = {
        "kpi_before_matches_reference": group["previous"] is not None
        and abs(group["previous"] - reference["before_hours"]) <= 0.01,
        "kpi_after_matches_reference": group["value"] is not None
        and abs(group["value"] - reference["after_hours"]) <= 0.01,
        "mttr_reduced": (group["delta_pct"] or 0) < 0,
        "control_group_stable": ctrl["delta_pct"] is not None and abs(ctrl["delta_pct"]) < 10,
        "csi_measurement_matches_kpi": measurement.get("delta_pct") is not None
        and abs(measurement["delta_pct"] - (group["delta_pct"] or 0)) <= 0.1,
        "csi_target_met": measurement.get("target_met") is True,
    }
    return EvidenceReport(
        now=now,
        days=days,
        before_hours=before_hours,
        after_hours=after_hours,
        reference=reference,
        kpi_overall={
            "value": mttr.value,
            "previous": mttr.previous,
            "delta_pct": mttr.delta_pct,
            "trend": mttr.trend,
            "count": mttr.count,
        },
        kpi_group=group,
        kpi_control=ctrl,
        improvement=imp,
        checks=checks,
    )


async def _improvement_lifecycle(
    session: AsyncSession, *, now: datetime, implemented: datetime, days: int
) -> dict[str, Any]:
    """CSI kaydı: `implemented` anında açılır/onaylanır/uygulanır, `now` anında ölçülür/kapanır."""
    agent = CurrentUser("sentetik.analist", frozenset({"agent"}))
    approver = CurrentUser("sentetik.onayci", frozenset({"approver"}))
    at = implemented
    imp = await improvements.create_improvement(
        session,
        ImprovementCreate(
            title=f"{TARGET_GROUP}: VPN tanı betiği ve bilgi makalesi (sentetik)",
            kpi_key="mttr",
            kpi_dimension="assignment_group",
            kpi_dimension_value=TARGET_GROUP,
            measure_days=days,
            benefit_estimate=4,
            effort=2,
        ),
        agent,
        now=at,
    )
    baseline = imp.baseline_value
    target = round(baseline * 0.7, 3) if baseline is not None else None
    if target is not None:
        imp = await improvements.update_improvement(
            session, imp.id, ImprovementUpdate(target_value=target), agent, now=at
        )
    imp = await improvements.approve(session, imp.id, approver, now=at)
    imp = await improvements.start(session, imp.id, agent, now=at)
    imp = await improvements.mark_implemented(session, imp.id, agent, at=at, now=at)
    imp = await improvements.measure(session, imp.id, agent, now=now)
    imp = await improvements.close(session, imp.id, approver, note="Sentetik kanıt", now=now)
    return {
        "number": imp.number,
        "state": str(getattr(imp.state, "value", imp.state)),
        "baseline_value": imp.baseline_value,
        "target_value": imp.target_value,
        "measured_value": imp.measured_value,
        "target_met": imp.target_met,
        "measurement": imp.measurement,
    }
