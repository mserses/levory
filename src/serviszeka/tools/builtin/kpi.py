"""Faz 4 raporlama araçları (R0, yalnızca okuma): kpi.report, sla.report, sla.forecast."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field

from serviszeka.domain.enums import RiskClass
from serviszeka.domain.types import UTCDateTime
from serviszeka.services import kpi as kpi_service
from serviszeka.services import sla_forecast
from serviszeka.tools.builtin._common import READERS, Args, AssignmentGroup
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolSpec

KPIKey = Literal[
    "mttr",
    "mtta",
    "fcr_rate",
    "sla_response_compliance",
    "sla_resolve_compliance",
    "backlog",
    "reopen_rate",
    "automation_rate",
    "ai_triage_acceptance",
    "change_success_rate",
    "emergency_change_share",
    "problem_known_error_lead_time",
]


def _unique(values: list[str] | None) -> list[str] | None:
    return list(dict.fromkeys(values)) if values else values


class WindowArgs(Args):
    days: int = Field(
        default=kpi_service.DEFAULT_DAYS, ge=1, le=kpi_service.MAX_DAYS, description="Pencere (gün)"
    )
    end: UTCDateTime | None = Field(
        default=None, description="Pencere sonu (hariç, ISO 8601); boşsa şimdi"
    )


class KPIReportArgs(WindowArgs):
    kpis: Annotated[list[KPIKey] | None, AfterValidator(_unique)] = Field(
        default=None, max_length=len(kpi_service.KPI_KEYS), description="Boşsa tüm KPI'lar"
    )


async def kpi_report(ctx: ToolContext, args: KPIReportArgs) -> dict[str, Any]:
    rep = await kpi_service.report(
        ctx.session, end=args.end or ctx.now, days=args.days, keys=args.kpis, now=ctx.now
    )
    return rep.model_dump(mode="json")


async def sla_report(ctx: ToolContext, args: WindowArgs) -> dict[str, Any]:
    rep = await kpi_service.sla_report(
        ctx.session, end=args.end or ctx.now, days=args.days, now=ctx.now
    )
    return rep.model_dump(mode="json")


class ForecastArgs(Args):
    assignment_group: AssignmentGroup
    min_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=200)


async def forecast(ctx: ToolContext, args: ForecastArgs) -> dict[str, Any]:
    rep = await sla_forecast.forecast(
        ctx.session,
        now=ctx.now,
        assignment_group=args.assignment_group,
        min_probability=args.min_probability,
        limit=args.limit,
    )
    return rep.model_dump(mode="json")


def register_all() -> None:
    R0 = RiskClass.R0
    for spec in (
        ToolSpec("kpi.report", "Hizmet yönetimi KPI'larını (MTTR, MTTA, SLA uyumu, backlog, "
                 "yeniden açılma, insansız talep, yapay zekâ kabulü, değişiklik başarısı...) "
                 "önceki pencereye göre eğilimle raporlar.", R0, KPIReportArgs, kpi_report, READERS),
        ToolSpec("sla.report", "Yanıt ve çözüm SLA uyumunu öncelik/grup kırılımıyla ve önceki "
                 "pencereye göre eğilimle raporlar.", R0, WindowArgs, sla_report, READERS),
        ToolSpec("sla.forecast", "Açık olayların çözüm SLA'sı ihlal olasılığını benzer olayların "
                 "geçmişine ve grup yüküne göre açıklamalı tahmin eder.", R0, ForecastArgs,
                 forecast, READERS),
    ):  # fmt: skip
        registry.register(spec)
