"""Faz 4 MTTR kanıtı (`serviszeka.mttr_evidence`, `evals/run_mttr_evidence.py`): SENTETİK
önce/sonra senaryosu KPI formülleriyle ve CSI ölçümüyle aynı MTTR düşüşünü vermeli."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from serviszeka import mttr_evidence
from serviszeka.models import Incident
from serviszeka.services import kpi

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def test_reference_mean_subtracts_paused_time():
    specs = mttr_evidence.cohort(
        group="network_ops", count=4, mean_hours=6.0, start=NOW - timedelta(days=4), end=NOW,
        paused_hours=1.0,
    )  # fmt: skip
    assert len(specs) == 4
    assert all(s.start <= s.resolved_at < s.end for s in specs)
    # etkin süre = duvar saati - duraklatma; ortalama hedeflenen değer
    assert mttr_evidence.reference_mean_hours(specs) == pytest.approx(6.0)
    assert all(
        (s.resolved_at - s.created_at).total_seconds() / 3600 - s.paused_hours
        == pytest.approx(s.effective_hours)
        for s in specs
    )


async def test_seeded_cohorts_match_kpi_formula(session):
    before = mttr_evidence.cohort(
        group="network_ops", count=6, mean_hours=10.0,
        start=NOW - timedelta(days=20), end=NOW - timedelta(days=10), paused_hours=2.0,
    )  # fmt: skip
    after = mttr_evidence.cohort(
        group="network_ops", count=6, mean_hours=4.0, start=NOW - timedelta(days=10), end=NOW
    )
    await mttr_evidence.seed(session, before + after)
    await session.flush()
    rows = (await session.execute(Incident.__table__.select())).all()
    assert len(rows) == 12
    cur = await kpi.compute(session, "mttr", kpi.Window(NOW - timedelta(days=10), NOW), NOW)
    prev = await kpi.compute(
        session, "mttr", kpi.Window(NOW - timedelta(days=20), NOW - timedelta(days=10)), NOW
    )
    assert cur.count == 6 and prev.count == 6
    assert cur.value == pytest.approx(4.0, abs=0.01)
    assert prev.value == pytest.approx(10.0, abs=0.01)


async def test_evidence_report_shows_mttr_reduction_and_passes():
    report = await mttr_evidence.run(now=NOW)
    data = report.to_dict()
    assert data["synthetic"] is True
    target = data["target_group"]
    # KPI raporu (önceki pencereyle) ve bağımsız aritmetik aynı değeri verir
    assert data["kpi"]["group"]["previous"] == pytest.approx(data["reference"]["before_hours"], abs=0.01)
    assert data["kpi"]["group"]["value"] == pytest.approx(data["reference"]["after_hours"], abs=0.01)
    assert data["kpi"]["group"]["delta_pct"] < -30
    # Kontrol grubu değişmez (iyileştirme yalnızca hedef gruba)
    assert abs(data["kpi"]["control"]["delta_pct"]) < 10
    # CSI kaydı: önce/sonra ölçümü aynı düşüşü gösterir, hedefe ulaşıldı
    m = data["improvement"]["measurement"]
    assert m["dimension"] == {"name": "assignment_group", "value": target}
    assert m["delta_pct"] == pytest.approx(data["kpi"]["group"]["delta_pct"], abs=0.1)
    assert m["target_met"] is True and data["improvement"]["state"] == "closed"
    assert data["gate"]["passed"] is True and report.passed


def test_script_runs_and_reports_synthetic_banner():
    out = subprocess.run(
        [sys.executable, str(ROOT / "evals" / "run_mttr_evidence.py"), "--now", NOW.isoformat()],
        check=False, capture_output=True, text=True, cwd=ROOT, timeout=120,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert "SENTETİK" in out.stdout and "GEÇTİ" in out.stdout.splitlines()[-1]
