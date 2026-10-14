"""Faz 4 — KPI raporu ve SLA ihlal tahmini API/araç şemaları (bkz. docs/faz4.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from serviszeka.domain.types import UTCDateTime

KPIUnit = Literal["hours", "percent", "count"]
KPIBetter = Literal["lower", "higher"]
KPITrend = Literal["improved", "worsened", "flat", "n/a"]


class KPIDefinitionRead(BaseModel):
    key: str
    name: str
    unit: KPIUnit
    better: KPIBetter
    formula: str


class KPIGroupStat(BaseModel):
    """Bir kırılım satırı (ör. öncelik=P2): değer ve payda (örneklem) büyüklüğü."""

    key: str | None
    value: float | None
    count: int


class KPIValueRead(BaseModel):
    key: str
    name: str
    unit: KPIUnit
    better: KPIBetter
    value: float | None = Field(description="Geçerli pencerede değer (veri yoksa null)")
    count: int = Field(description="Payda / örneklem büyüklüğü")
    previous: float | None = Field(description="Önceki (eşit uzunlukta) pencerede değer")
    previous_count: int
    delta: float | None = Field(description="value - previous")
    delta_pct: float | None = Field(description="(value - previous) / previous * 100")
    trend: KPITrend
    details: dict[str, Any] = Field(default_factory=dict)
    breakdown: dict[str, list[KPIGroupStat]] = Field(default_factory=dict)


class KPIWindowRead(BaseModel):
    """Yarı açık pencere [start, end), UTC."""

    start: UTCDateTime
    end: UTCDateTime


class KPIReportRead(BaseModel):
    window: KPIWindowRead
    previous_window: KPIWindowRead
    generated_at: UTCDateTime
    kpis: list[KPIValueRead]


# --- SLA ihlal tahmini ---------------------------------------------------------------------------

ForecastBasis = Literal["priority_group", "priority", "none", "breached"]


class ForecastReason(BaseModel):
    code: str
    message: str
    weight: float = Field(description="Log-olasılık oranına katkının mutlak değeri (sıralama için)")


class BreachForecastRead(BaseModel):
    incident_id: str
    number: str
    priority: str
    assignment_group: str | None
    status: str
    paused: bool
    resolve_due_at: UTCDateTime | None
    probability: float | None = Field(description="Çözüm SLA'sının ihlal olasılığı (0-1)")
    expected_resolve_at: UTCDateTime | None
    elapsed_hours: float = Field(description="Duraklatma hariç geçen süre")
    remaining_hours: float = Field(description="Duraklatma hariç kalan hedef süre (negatif: geçti)")
    target_hours: float
    basis: ForecastBasis
    sample_size: int = Field(description="Karşılaştırılan olay sayısı (çözülen + açık)")
    sample_resolved: int
    sample_censored: int = Field(description="Hâlâ açık (sansürlü, Kaplan–Meier) olay sayısı")
    load_ratio: float | None = Field(description="Grubun şu anki açık kaydı / olağan ortalaması")
    reasons: list[ForecastReason]


class ForecastReportRead(BaseModel):
    generated_at: UTCDateTime
    lookback_days: int
    min_sample: int
    count: int
    items: list[BreachForecastRead]


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    count: int
    mean_predicted: float | None
    observed_rate: float | None


class BacktestRead(BaseModel):
    split_at: datetime
    end: datetime
    lookback_days: int
    train_size: int
    test_size: int = Field(description="Tahmin yapılabilen (yeterli örneklemli) test olayı sayısı")
    skipped: int = Field(description="Örneklem yetersiz olduğu için tahmin yapılamayan test olayı")
    brier: float | None
    baseline_brier: float | None = Field(description="Sabit oran (eğitim ihlal oranı) tahmini")
    baseline_rate: float | None
    skill: float | None = Field(description="1 - brier / baseline_brier (pozitif: daha iyi)")
    calibration: list[CalibrationBin]
