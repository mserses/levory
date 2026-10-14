"""Faz 4 sürekli iyileştirme (CSI): kayıt, durum makinesi, KPI ölçümü, CSI ajanı, API, araçlar."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from _auth import ADMIN, dev_headers
from _auth import AGENT as H_AGENT
from _auth import APPROVER as H_APPROVER
from _csi import (
    AGENT,
    AI_CSI,
    APPROVER,
    EXPECTED_SIGNALS,
    NOW,
    D,
    Frozen,
    mttr_rows,
    seed_signals,
)
from sqlalchemy import func, select

from serviszeka import db
from serviszeka.agents import improvement as csi_agent
from serviszeka.config import Settings
from serviszeka.domain.enums import ImprovementSource, ImprovementState
from serviszeka.llm import FakeLLM, LLMError
from serviszeka.main import app
from serviszeka.models import ActionRequest, AIDecision, AuditLog, Improvement
from serviszeka.services import improvements as svc
from serviszeka.services.improvement_schemas import ImprovementCreate, ImprovementUpdate
from serviszeka.tools import ToolStatus, execute_tool

LOOSE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0)
SHADOW = Settings(ai_shadow_mode=True)
T0 = NOW - 60 * D  # iyileştirme kaydı (taban penceresi [T0-30g, T0))
T_IMPL = T0 + 10 * D  # uygulama tamamlandı (ölçüm penceresi [T_IMPL, T_IMPL+30g))


@pytest.fixture
def frozen(monkeypatch) -> Frozen:
    return Frozen(monkeypatch)


def _narrator(text: str = "Grup MTTR'si belirgin biçimde arttı; kök nedenler incelenmeli."):
    return FakeLLM(lambda system, user: {"narrative": text})


async def _mttr_improvement(session, **kw) -> Improvement:
    """network_ops MTTR'si taban penceresinde 10 sa; hedef 6 sa."""
    await mttr_rows(session, group="network_ops", category="ag", hours=10, start=T0 - 20 * D)
    data = ImprovementCreate(
        title="Ağ ekibinde MTTR düşürme",
        description="Runbook ve izleme iyileştirmesi",
        source=ImprovementSource.KPI,
        kpi_key="mttr",
        kpi_dimension="assignment_group",
        kpi_dimension_value="network_ops",
        target_value=6.0,
        benefit_estimate=5,
        effort=2,
        **kw,
    )
    imp = await svc.create_improvement(session, data, AGENT, now=T0)
    await session.commit()
    return imp


# --- kayıt ve durum makinesi -------------------------------------------------------------------


async def test_create_numbers_and_computes_kpi_baseline_and_score(session):
    imp = await _mttr_improvement(session)
    assert imp.number == "CSI0000001" and imp.state == ImprovementState.IDEA
    assert imp.baseline_value == 10.0
    assert imp.baseline_start == T0 - 30 * D and imp.baseline_end == T0
    assert imp.priority_score == 50.0  # fayda 5 / efor 2 * 20
    assert imp.created_by == imp.detected_by == "ayse" and imp.version == 1
    second = await svc.create_improvement(session, ImprovementCreate(title="El ile"), AGENT, now=T0)
    assert second.number == "CSI0000002" and second.source == ImprovementSource.MANUAL
    assert second.baseline_value is None and second.priority_score == 20.0
    acts = (await session.scalars(select(AuditLog.action))).all()
    assert acts.count("improvement.create") == 2


@pytest.mark.parametrize(
    "fields",
    [
        {"kpi_key": "uydurma"},
        {"kpi_dimension": "assignment_group", "kpi_dimension_value": "dba"},  # kpi_key yok
        {"kpi_key": "mttr", "kpi_dimension": "assignment_group"},  # değer yok
        {"kpi_key": "mtta", "kpi_dimension": "category", "kpi_dimension_value": "ag"},
        {"benefit_estimate": 6},
        {"effort": 0},
        {"target_value": float("nan")},
        {"source": "tahmin"},
        {"bilinmeyen": 1},
    ],
)
def test_create_schema_rejects_invalid(fields):
    with pytest.raises(ValueError):
        ImprovementCreate(title="X", **fields)


async def test_state_machine_and_human_only_steps(session):
    imp = await _mttr_improvement(session)
    # Yapay zekâ onaylayamaz, başlatamaz, uygulandı işaretleyemez, kapatamaz, reddedemez
    for fn, extra in (
        (svc.approve, {}), (svc.start, {}), (svc.mark_implemented, {}),
        (svc.close, {"note": "x"}), (svc.reject, {"note": "x"}),
    ):  # fmt: skip
        with pytest.raises(svc.ImprovementForbiddenError):
            await fn(session, imp.id, AI_CSI, now=T0, **extra)
    # Geçersiz sıra
    with pytest.raises(svc.InvalidImprovementTransitionError):
        await svc.start(session, imp.id, AGENT, now=T0)
    with pytest.raises(svc.InvalidImprovementTransitionError):
        await svc.close(session, imp.id, APPROVER, now=T0)
    with pytest.raises(svc.ImprovementValidationError):
        await svc.reject(session, imp.id, APPROVER, note="  ", now=T0)
    # Sürüm çakışması
    with pytest.raises(svc.ImprovementVersionConflictError):
        await svc.approve(session, imp.id, APPROVER, expected_version=7, now=T0)
    imp = await svc.approve(session, imp.id, APPROVER, now=T0)
    assert imp.state == ImprovementState.APPROVED and imp.approved_by == "onayci"
    imp = await svc.start(session, imp.id, AGENT, now=T0 + D)
    assert imp.state == ImprovementState.IN_PROGRESS and imp.started_at == T0 + D
    imp = await svc.mark_implemented(session, imp.id, AGENT, at=T_IMPL, now=T_IMPL)
    assert imp.implemented_at == T_IMPL and imp.state == ImprovementState.IN_PROGRESS
    # Onaydan sonra KPI tanımı ve hedef değiştirilemez (ölçüm oyunlanamaz)
    with pytest.raises(svc.ImprovementValidationError):
        await svc.update_improvement(session, imp.id, ImprovementUpdate(target_value=11.0), AGENT)
    imp = await svc.update_improvement(session, imp.id, ImprovementUpdate(owner="mehmet"), AGENT)
    assert imp.owner == "mehmet"
    # Ölçülmeden kapatılamaz; iptal (red) notla olur
    with pytest.raises(svc.InvalidImprovementTransitionError):
        await svc.close(session, imp.id, APPROVER, now=T_IMPL)
    imp = await svc.reject(session, imp.id, APPROVER, note="Öncelik değişti", now=T_IMPL)
    assert imp.state == ImprovementState.REJECTED and imp.resolution == "Öncelik değişti"
    with pytest.raises(svc.InvalidImprovementTransitionError):
        await svc.approve(session, imp.id, APPROVER, now=T_IMPL)


# --- ölçüm: MTTR'de ölçülebilir düşüş ----------------------------------------------------------


async def _in_progress(session) -> Improvement:
    imp = await _mttr_improvement(session)
    await svc.approve(session, imp.id, APPROVER, now=T0)
    await svc.start(session, imp.id, AGENT, now=T0)
    imp = await svc.mark_implemented(session, imp.id, AGENT, at=T_IMPL, now=T_IMPL)
    await session.commit()
    return imp


async def test_measure_records_mttr_reduction_and_target_met(session):
    imp = await _in_progress(session)
    # Ölçüm penceresi dolmadan ölçülemez
    with pytest.raises(svc.ImprovementNotReadyError):
        await svc.measure(session, imp.id, AGENT, now=T_IMPL + 20 * D)
    # Uygulamadan sonra ağ ekibi olayları 5 saatte çözülüyor
    await mttr_rows(session, group="network_ops", category="ag", hours=5, start=T_IMPL + D)
    # Başka grubun olayları ölçümü etkilemez
    await mttr_rows(session, group="dba", category="veritabani", hours=50, start=T_IMPL + D)
    imp = await svc.measure(session, imp.id, AGENT, now=T_IMPL + 31 * D)
    assert imp.state == ImprovementState.MEASURED
    assert imp.measured_value == 5.0 and imp.target_met is True
    m = imp.measurement
    assert m["kpi_key"] == "mttr" and m["better"] == "lower"
    assert m["before"]["value"] == 10.0 and m["after"]["value"] == 5.0
    assert m["after"]["count"] == 6
    assert m["delta"] == -5.0 and m["delta_pct"] == -50.0 and m["improved"] is True
    assert m["after"]["window"] == {
        "start": T_IMPL.isoformat(), "end": (T_IMPL + 30 * D).isoformat()
    }  # fmt: skip
    imp = await svc.close(session, imp.id, APPROVER, note="Hedef tuttu", now=T_IMPL + 32 * D)
    assert imp.state == ImprovementState.CLOSED and imp.closed_by == "onayci"
    acts = (await session.scalars(select(AuditLog.action))).all()
    assert {"improvement.measure", "improvement.close"} <= set(acts)


async def test_measure_target_missed_and_no_data(session):
    imp = await _in_progress(session)
    with pytest.raises(svc.ImprovementValidationError):  # ölçüm penceresinde veri yok
        await svc.measure(session, imp.id, AGENT, now=T_IMPL + 31 * D)
    await mttr_rows(session, group="network_ops", category="ag", hours=8, start=T_IMPL + D)
    imp = await svc.measure(session, imp.id, AGENT, now=T_IMPL + 31 * D)
    assert imp.measured_value == 8.0 and imp.target_met is False
    assert imp.measurement["improved"] is True  # iyileşti ama hedefe ulaşmadı


async def test_measure_without_target_uses_direction(session):
    await mttr_rows(session, group="network_ops", category="ag", hours=10, start=T0 - 20 * D)
    imp = await svc.create_improvement(
        session, ImprovementCreate(title="Uyum", kpi_key="mttr"), AGENT, now=T0
    )
    await svc.approve(session, imp.id, APPROVER, now=T0)
    await svc.start(session, imp.id, AGENT, now=T0)
    await svc.mark_implemented(session, imp.id, AGENT, at=T_IMPL, now=T_IMPL)
    await mttr_rows(session, group="network_ops", category="ag", hours=12, start=T_IMPL + D)
    imp = await svc.measure(session, imp.id, AGENT, now=T_IMPL + 31 * D)
    assert imp.target_met is False and imp.measurement["improved"] is False


# --- CSI ajanı: deterministik sinyaller --------------------------------------------------------


async def test_detect_signals_finds_each_deterministic_signal(session, frozen):
    ids = await seed_signals(session, frozen)
    signals = await csi_agent.detect_signals(session, now=NOW)
    keys = {s.key for s in signals}
    expected = EXPECTED_SIGNALS | {
        f"kb_unhelpful:{ids['article_id']}",
        f"known_error_no_fix:{ids['problem_id']}",
        f"pir_failed_change:{ids['change_id']}",
    }
    assert keys == expected
    by = {s.key.split(":")[0]: s for s in signals}
    mttr = by["mttr_increase"]
    assert mttr.kpi_key == "mttr" and mttr.dimension == ("assignment_group", "dba")
    assert mttr.evidence["current"] == 8.0 and mttr.evidence["previous"] == 4.0
    assert mttr.evidence["delta_pct"] == 100.0 and mttr.target_value == 4.0
    assert by["sla_breaches"].evidence["breached"] == 4
    assert by["sla_breaches"].evidence["compliance"] == 0.0
    assert by["ai_triage_acceptance"].source == ImprovementSource.AI
    assert by["reopen_rate"].evidence == {
        **by["reopen_rate"].evidence, "reopened": 2, "population": 5, "rate": 40.0
    }
    assert by["kb_unhelpful"].evidence["suggested"] == 5
    assert by["kb_unhelpful"].evidence["helped"] == 0
    assert by["sla_forecast"].evidence["at_risk"] == 3
    assert by["known_error_no_fix"].source == ImprovementSource.PROBLEM
    assert by["known_error_no_fix"].problem_id == ids["problem_id"]
    assert by["pir_failed_change"].source == ImprovementSource.PIR
    assert by["pir_failed_change"].change_id == ids["change_id"]
    # Deterministik ve salt okuma: aynı sonuç, hiçbir şey yazılmadı
    again = await csi_agent.detect_signals(session, now=NOW)
    assert [s.key for s in again] == [s.key for s in signals]
    assert not session.new and not session.dirty
    assert await session.scalar(select(func.count()).select_from(Improvement)) == 0


async def test_no_signals_on_quiet_data(session):
    await mttr_rows(session, group="dba", category="veritabani", hours=4, start=NOW - 55 * D)
    await mttr_rows(session, group="dba", category="veritabani", hours=4.5, start=NOW - 25 * D)
    await session.commit()
    assert await csi_agent.detect_signals(session, now=NOW) == []


async def test_agent_proposes_ideas_with_llm_narrative_and_dedupes(session, frozen):
    await seed_signals(session, frozen)
    llm = _narrator()
    results = await csi_agent.propose_improvements(session, llm=llm, settings=LOOSE, now=NOW)
    await session.commit()
    assert results and all(r.status == ToolStatus.EXECUTED for r in results), results
    rows = (await session.scalars(select(Improvement))).all()
    assert len(rows) == len(results) == 8
    for imp in rows:
        # Yapay zekâ yalnızca fikir önerir; asla onaylamaz
        assert imp.state == ImprovementState.IDEA and imp.approved_by is None
        assert imp.detected_by == "ai:csi" and imp.signal_key and imp.evidence
        assert "Grup MTTR'si belirgin" in imp.description
        assert "Kanıt:" in imp.description  # sayılar LLM'den değil, deterministik
    mttr = next(i for i in rows if i.signal_key == "mttr_increase:assignment_group:dba")
    assert mttr.kpi_key == "mttr" and mttr.baseline_value == 8.0 and mttr.target_value == 4.0
    assert mttr.source == ImprovementSource.KPI
    # LLM'e giden istem maskeli ve kanıt sayılarını içeriyor
    assert any("+100.0%" in c["user"] or "100.0" in c["user"] for c in llm.calls)
    # Her karar politika -> AIDecision -> denetim zincirinden geçti
    decisions = (
        await session.scalars(select(AIDecision).where(AIDecision.agent == "csi"))
    ).all()
    assert len(decisions) == 8 and {d.action for d in decisions} == {"create_improvement"}
    # Aynı sinyal ikinci kez önerilmez
    again = await csi_agent.propose_improvements(session, llm=llm, settings=LOOSE, now=NOW)
    assert again == []


async def test_agent_in_shadow_mode_only_opens_action_requests(session, frozen):
    await seed_signals(session, frozen)
    results = await csi_agent.propose_improvements(
        session, llm=_narrator(), settings=SHADOW, now=NOW
    )
    await session.commit()
    assert results and all(r.status == ToolStatus.PENDING_APPROVAL for r in results)
    assert await session.scalar(select(func.count()).select_from(Improvement)) == 0
    pending = (await session.scalars(select(ActionRequest))).all()
    assert {a.tool for a in pending} == {"csi.create_improvement"}
    # Bekleyen öneri yinelenmez
    assert await csi_agent.propose_improvements(
        session, llm=_narrator(), settings=SHADOW, now=NOW
    ) == []


async def test_agent_falls_back_to_deterministic_narrative_when_llm_fails(session, frozen):
    await seed_signals(session, frozen)

    def boom(system, user):
        raise LLMError("kapalı")

    results = await csi_agent.propose_improvements(
        session, llm=FakeLLM(boom), settings=LOOSE, now=NOW
    )
    assert len(results) == 8 and all(r.status == ToolStatus.EXECUTED for r in results)
    rows = (await session.scalars(select(Improvement))).all()
    assert all(i.description.startswith("Otomatik tespit:") for i in rows)


async def test_agent_ignores_non_text_llm_output(session, frozen):
    await seed_signals(session, frozen)
    llm = FakeLLM([{"narrative": 42}, {"narrative": "x" * 50000}])
    await csi_agent.propose_improvements(session, llm=llm, settings=LOOSE, now=NOW)
    rows = (await session.scalars(select(Improvement))).all()
    assert len(rows) == 8 and all(len(i.description) <= 20000 for i in rows)
    fallback = [i for i in rows if i.description.startswith("Otomatik tespit:")]
    assert len(fallback) == 1  # sayı dönen ilk yanıt yok sayıldı
    assert all("x" * 100 in i.description for i in rows if i not in fallback)


async def test_agent_measures_due_improvements_and_never_closes(session):
    imp = await _in_progress(session)
    await mttr_rows(session, group="network_ops", category="ag", hours=5, start=T_IMPL + D)
    await session.commit()
    # Pencere dolmadan ölçüm yok
    assert await csi_agent.measure_due(session, settings=LOOSE, now=T_IMPL + 10 * D) == []
    results = await csi_agent.measure_due(session, settings=LOOSE, now=T_IMPL + 31 * D)
    await session.commit()
    assert [r.status for r in results] == [ToolStatus.EXECUTED]
    await session.refresh(imp)
    assert imp.state == ImprovementState.MEASURED and imp.target_met is True
    assert imp.closed_at is None
    assert await csi_agent.measure_due(session, settings=LOOSE, now=T_IMPL + 40 * D) == []


async def test_review_runs_propose_and_measure(session, frozen):
    await seed_signals(session, frozen)
    out = await csi_agent.run_review(session, llm=_narrator(), settings=LOOSE, now=NOW)
    assert out == {"signals": 8, "proposed": 8, "measured": 0, "pending_approval": 0}


async def test_scheduler_csi_job_runs_review(session, frozen):
    from serviszeka import scheduler

    await seed_signals(session, frozen)
    names = [j.name for j in scheduler.DEFAULT_JOBS]
    assert "csi_review" in names
    job = next(j for j in scheduler.DEFAULT_JOBS if j.name == "csi_review")
    assert job.interval >= 3600

    class _N:
        async def send(self, *a, **kw):
            return None

    ctx = scheduler.JobContext(
        session, NOW, Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0,
                               llm_provider="fake", environment="development"), _N()
    )  # fmt: skip
    result = await job.fn(ctx)
    assert result["proposed"] == 8
    assert await session.scalar(select(func.count()).select_from(Improvement)) == 8


# --- araçlar ------------------------------------------------------------------------------------


async def test_tools_list_create_measure(session):
    imp = await _in_progress(session)
    await mttr_rows(session, group="network_ops", category="ag", hours=5, start=T_IMPL + D)
    await session.commit()
    r = await execute_tool(session, AGENT, "csi.list", {"state": "in_progress"}, settings=LOOSE)
    assert r.status == ToolStatus.EXECUTED and r.result["count"] == 1
    r = await execute_tool(
        session, AGENT, "csi.create_improvement",
        {"title": "Yeni fikir", "kpi_key": "backlog", "source": "manual"},
        settings=LOOSE, now=NOW,
    )  # fmt: skip
    assert r.status == ToolStatus.EXECUTED and r.result["number"] == "CSI0000002"
    r = await execute_tool(
        session, AGENT, "csi.measure_improvement", {"improvement_id": imp.id},
        settings=LOOSE, now=T_IMPL + 31 * D,
    )  # fmt: skip
    assert r.status == ToolStatus.EXECUTED and r.result["state"] == "measured"
    r = await execute_tool(
        session, AGENT, "csi.detect_signals", {"window_days": 30}, settings=LOOSE, now=NOW
    )
    assert r.status == ToolStatus.EXECUTED and "signals" in r.result


async def test_tool_ai_create_cannot_set_state_or_approver(session):
    from serviszeka.tools import ToolValidationError

    for extra in ({"state": "approved"}, {"approved_by": "x"}, {"measured_value": 1.0}):
        with pytest.raises(ToolValidationError):
            await execute_tool(
                session, AI_CSI, "csi.create_improvement", {"title": "Fikir", **extra},
                confidence=1.0, settings=LOOSE,
            )  # fmt: skip
    assert await session.scalar(select(func.count()).select_from(Improvement)) == 0


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture
async def client(session) -> AsyncIterator[httpx.AsyncClient]:
    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    # API gerçek saati kullanır: taban penceresi [şimdi - 30g, şimdi)
    start = datetime.now(UTC) - 20 * D
    await mttr_rows(session, group="network_ops", category="ag", hours=10, start=start)
    await session.commit()
    app.dependency_overrides[db.get_session] = _get_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def test_api_roles_and_lifecycle(client):
    body = {
        "title": "MTTR düşürme", "kpi_key": "mttr", "kpi_dimension": "assignment_group",
        "kpi_dimension_value": "network_ops", "target_value": 6, "source": "kpi",
    }  # fmt: skip
    for headers in (dev_headers("ali", "requester"), dev_headers("bot", "ai_agent")):
        assert (await client.post("/improvements", json=body, headers=headers)).status_code == 403
        assert (await client.get("/improvements", headers=headers)).status_code == 403
    r = await client.post("/improvements", json=body, headers=H_AGENT)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["number"] == "CSI0000001" and imp["state"] == "idea"
    assert imp["baseline_value"] == 10.0 and r.headers["ETag"] == '"1"'
    iid = imp["id"]
    # Agent onaylayamaz/kapatamaz/reddedemez
    for path in ("approve", "close", "reject"):
        r = await client.post(f"/improvements/{iid}/{path}", json={"note": "x"}, headers=H_AGENT)
        assert r.status_code == 403, path
    r = await client.post(
        f"/improvements/{iid}/approve", json={}, headers={**H_APPROVER, "If-Match": '"9"'}
    )
    assert r.status_code == 409
    r = await client.post(f"/improvements/{iid}/approve", json={}, headers=H_APPROVER)
    assert r.status_code == 200 and r.json()["state"] == "approved"
    assert r.json()["approved_by"] == "sef.ali"
    r = await client.post(f"/improvements/{iid}/start", json={}, headers=H_AGENT)
    assert r.status_code == 200 and r.json()["state"] == "in_progress"
    r = await client.post(f"/improvements/{iid}/implemented", json={}, headers=H_AGENT)
    assert r.status_code == 200 and r.json()["implemented_at"]
    r = await client.post(f"/improvements/{iid}/measure", headers=H_AGENT)
    assert r.status_code == 409  # pencere dolmadı
    r = await client.post(f"/improvements/{iid}/close", json={}, headers=ADMIN)
    assert r.status_code == 422  # ölçülmeden kapatılamaz
    r = await client.post(f"/improvements/{iid}/reject", json={"note": "İptal"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["state"] == "rejected"
    r = await client.get("/improvements", params={"state": "rejected"}, headers=H_APPROVER)
    assert [i["id"] for i in r.json()] == [iid]
    assert (await client.get("/improvements/yok", headers=H_AGENT)).status_code == 404
    r = await client.patch(f"/improvements/{iid}", json={"owner": "x"}, headers=H_AGENT)
    assert r.status_code == 422  # kapalı kayıt değişmez


async def test_api_patch_and_signals_and_review(client):
    r = await client.post("/improvements", json={"title": "Fikir"}, headers=H_APPROVER)
    iid = r.json()["id"]
    r = await client.patch(
        f"/improvements/{iid}", json={"benefit_estimate": 4, "effort": 1},
        headers={**H_AGENT, "If-Match": '"1"'},
    )  # fmt: skip
    assert r.status_code == 200 and r.json()["priority_score"] == 80.0
    assert r.json()["version"] == 2
    r = await client.post("/improvements/signals", json={"window_days": 30}, headers=H_AGENT)
    assert r.status_code == 200 and r.json()["window_days"] == 30
    assert (
        await client.post("/improvements/signals", json={}, headers=dev_headers("a", "requester"))
    ).status_code == 403
