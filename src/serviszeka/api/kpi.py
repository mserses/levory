"""Faz 4 — KPI raporu (`/kpi`) ve SLA raporu / ihlal tahmini (`/sla`) uç noktaları.

Okuma: agent / approver / admin (`ReaderUser`); requester (self-servis) ve yapay zekâ belirteci
(`ai_agent`) göremez (403) — yapay zekâ aynı verilere R0 araçlarla (`kpi.report`, `sla.report`,
`sla.forecast`) ulaşır. Tanımlar ve formüller: `services/kpi.py`, `services/sla_forecast.py`,
`docs/faz4.md`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import ReaderUser, get_current_user
from serviszeka.db import get_session
from serviszeka.services import kpi as kpi_service
from serviszeka.services import sla_forecast
from serviszeka.services.kpi_schemas import (
    BacktestRead,
    ForecastReportRead,
    KPIDefinitionRead,
    KPIReportRead,
)

router = APIRouter(prefix="/kpi", tags=["kpi"], dependencies=[Depends(get_current_user)])
sla_router = APIRouter(prefix="/sla", tags=["sla"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
Days = Annotated[int, Query(ge=1, le=kpi_service.MAX_DAYS, description="Pencere uzunluğu (gün)")]
End = Annotated[
    datetime | None,
    Query(description="Pencere sonu (hariç); saat dilimsiz değer UTC kabul edilir, boşsa şimdi"),
]


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("", response_model=KPIReportRead)
async def kpi_report(
    session: SessionDep,
    _user: ReaderUser,
    days: Days = kpi_service.DEFAULT_DAYS,
    end: End = None,
    kpi: Annotated[
        list[str] | None, Query(description="Tekrarlanabilir KPI anahtarı; boşsa tümü")
    ] = None,
) -> KPIReportRead:
    """KPI'lar: geçerli pencere ve önceki eşit pencereye göre eğilim."""
    try:
        return await kpi_service.report(session, end=end, days=days, keys=kpi)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/definitions", response_model=list[KPIDefinitionRead])
async def kpi_definitions(_user: ReaderUser) -> list[KPIDefinitionRead]:
    return kpi_service.definitions()


@sla_router.get("/report", response_model=KPIReportRead)
async def sla_report(
    session: SessionDep, _user: ReaderUser, days: Days = kpi_service.DEFAULT_DAYS, end: End = None
) -> KPIReportRead:
    """Yanıt ve çözüm SLA uyumu (öncelik / grup kırılımı, önceki pencereye göre eğilim)."""
    try:
        return await kpi_service.sla_report(session, end=end, days=days)
    except ValueError as exc:
        raise _bad_request(exc) from exc


@sla_router.get("/forecast", response_model=ForecastReportRead)
async def sla_forecast_list(
    session: SessionDep,
    _user: ReaderUser,
    assignment_group: Annotated[str | None, Query(max_length=100)] = None,
    min_probability: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    lookback_days: Annotated[int, Query(ge=7, le=kpi_service.MAX_DAYS)] = (
        sla_forecast.LOOKBACK_DAYS
    ),
) -> ForecastReportRead:
    """Açık olayların çözüm SLA'sı ihlal olasılığı, beklenen çözüm zamanı ve gerekçeleri."""
    return await sla_forecast.forecast(
        session,
        assignment_group=assignment_group,
        min_probability=min_probability,
        limit=limit,
        lookback_days=lookback_days,
    )


@sla_router.get("/forecast/backtest", response_model=BacktestRead)
async def sla_forecast_backtest(
    session: SessionDep,
    _user: ReaderUser,
    split_at: Annotated[datetime, Query(description="Eğitim/test bölme anı")],
    end: End = None,
    lookback_days: Annotated[int, Query(ge=7, le=kpi_service.MAX_DAYS)] = (
        sla_forecast.LOOKBACK_DAYS
    ),
) -> BacktestRead:
    """Tahmin kalibrasyonu: zaman tabanlı bölmeyle Brier skoru ve kalibrasyon tablosu."""
    try:
        return await sla_forecast.backtest(
            session, split_at=split_at, end=end, lookback_days=lookback_days
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
