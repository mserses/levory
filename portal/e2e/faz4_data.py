"""Faz 4 E2E verisi (yalnızca E2E; SENTETİK): KPI panosu ve CSI senaryosu için geçmiş olaylar.

Playwright `faz4` backend'i (`e2e/start-backend.mjs`, `E2E_PROFILE=faz4`) açılmadan önce:

    uv run python portal/e2e/faz4_data.py seed --db <e2e.db>

- `network_ops` (CSI hedefi, `SZ_CSI_WINDOW_DAYS=7`): önceki 7 günde 8 olay ~4 sa, son 7 günde
  8 olay ~10 sa -> CSI ajanı `mttr_increase:assignment_group:network_ops` sinyali bulur.
- Diğer gruplar (haftada < 5 olay: sinyal üretmez) son 60 güne yayılır; birkaç açık olay (birikim).

Ölçüm penceresinin geçmesini beklemek yerine (`measure_days` = 7 gün) senaryo saati ileri sarar:

    uv run python portal/e2e/faz4_data.py time-travel --db <e2e.db> --improvement <id> --days 8

`network_ops` sentetik olayları ve iyileştirmenin zaman damgaları `days` gün geriye kaydırılır
(= o kadar gün geçti), ardından uygulama sonrası pencereye hızlı çözülen (~3 sa) olaylar eklenir.
Denetim zinciri ve başka kayıtlara dokunulmaz. Olay üreteci `serviszeka.mttr_evidence` ile ortaktır.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from serviszeka import models  # noqa: F401  (tabloları kaydeder)
from serviszeka.db import Base
from serviszeka.domain.enums import IncidentStatus, Priority
from serviszeka.models import Improvement, Incident
from serviszeka.mttr_evidence import cohort, seed
from serviszeka.services import incidents as incident_service

TARGET = "network_ops"
D = timedelta(days=1)
H = timedelta(hours=1)
_INCIDENT_TIMES = (
    "created_at", "updated_at", "responded_at", "resolved_at", "response_due_at",
    "resolve_due_at", "sla_paused_at",
)  # fmt: skip
_IMPROVEMENT_TIMES = (
    "created_at", "updated_at", "baseline_start", "baseline_end", "approved_at", "started_at",
    "implemented_at",
)  # fmt: skip


async def _with_session(db: str, fn) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await fn(session)
            await session.commit()
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    specs = [
        # CSI hedefi: son 7 günde MTTR ~2,5 kat
        *cohort(group=TARGET, count=8, mean_hours=4.0, start=now - 14 * D, end=now - 7 * D,
                paused_hours=0.5),
        *cohort(group=TARGET, count=8, mean_hours=10.0, start=now - 7 * D, end=now - 2 * H),
        # Pano için diğer gruplar (haftada < 5 olay)
        *cohort(group="end_user_support", count=14, mean_hours=6.0, start=now - 60 * D,
                end=now - 30 * D, category="donanim", service="is_istasyonu"),
        *cohort(group="end_user_support", count=14, mean_hours=5.0, start=now - 30 * D,
                end=now - 14 * D, category="donanim", service="is_istasyonu", paused_hours=1.0),
        *cohort(group="dba", count=8, mean_hours=7.0, start=now - 60 * D, end=now - 16 * D,
                category="veritabani", service="veri_platformu",
                priorities=(Priority.P2, Priority.P3)),
        *cohort(group="service_desk_l1", count=12, mean_hours=2.0, start=now - 45 * D,
                end=now - 15 * D, category="erisim_kimlik", service="kimlik",
                priorities=(Priority.P4, Priority.P5)),
    ]  # fmt: skip
    await seed(session, specs)
    # Açık olaylar (birikim yaşlanması)
    base = int((await incident_service.next_number(session))[3:])
    for i, (age, group, priority) in enumerate(
        [(2 * D, "end_user_support", Priority.P3), (5 * D, "dba", Priority.P4),
         (12 * H, "service_desk_l1", Priority.P3), (40 * D, "end_user_support", Priority.P5)]
    ):  # fmt: skip
        created = now - age
        incident = Incident(
            number=f"INC{base + i:07d}", title=f"[Sentetik] açık olay {base + i}",
            description="Sentetik E2E verisi.", caller="sentetik", category="donanim",
            priority=priority, status=IncidentStatus.IN_PROGRESS, assignment_group=group,
            responded_at=created + H, created_at=created, updated_at=created,
        )  # fmt: skip
        incident_service.apply_sla(incident)
        session.add(incident)


def _shift(row: object, fields: tuple[str, ...], delta: timedelta) -> None:
    for name in fields:
        value = getattr(row, name, None)
        if value is not None:
            setattr(row, name, value - delta)


async def _time_travel(session: AsyncSession, improvement_id: str, days: int) -> None:
    delta = days * D
    imp = await session.get(Improvement, improvement_id)
    if imp is None:
        raise SystemExit(f"iyileştirme yok: {improvement_id}")
    if imp.implemented_at is None:
        raise SystemExit("önce 'uygulandı' işaretlenmeli")
    _shift(imp, _IMPROVEMENT_TIMES, delta)
    rows = await session.scalars(
        select(Incident).where(Incident.assignment_group == TARGET, Incident.caller == "sentetik")
    )
    for incident in rows:
        _shift(incident, _INCIDENT_TIMES, delta)
    await session.flush()
    # Uygulama sonrası: ölçüm penceresi [implemented_at, implemented_at + measure_days)
    start = imp.implemented_at.replace(tzinfo=imp.implemented_at.tzinfo or UTC) + 12 * H
    end = start + (imp.measure_days * D) - 24 * H
    await seed(session, cohort(group=TARGET, count=6, mean_hours=3.0, start=start, end=end))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--db", required=True)
    t = sub.add_parser("time-travel")
    t.add_argument("--db", required=True)
    t.add_argument("--improvement", required=True)
    t.add_argument("--days", type=int, required=True)
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(_with_session(args.db, _seed))
    else:
        asyncio.run(
            _with_session(args.db, lambda s: _time_travel(s, args.improvement, args.days))
        )


if __name__ == "__main__":
    main()
