"""Tek komutla demo (`serviszeka.demo_seed`, `docker-compose.demo.yml`, `scripts/demo.sh`).

Tohum yalnızca `SZ_ENVIRONMENT=development` iken çalışır, gerçekçi Türkçe veriyi tek işlemde
yazar ve ikinci çalıştırmada hiçbir şey eklemez (idempotent). Ağ yok: sahte LLM ve sahte dizin.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

from serviszeka import audit, demo_seed
from serviszeka.config import get_settings
from serviszeka.domain.enums import (
    ActionRequestState,
    ChangeState,
    DecisionOutcome,
    EventGroupState,
    ImprovementState,
    IncidentStatus,
    ProblemState,
    ServiceRequestState,
    TicketKind,
)
from serviszeka.models import (
    ActionRequest,
    AIDecision,
    AuditLog,
    ChangeApproval,
    ChangeRequest,
    CIRelationship,
    ConfigurationItem,
    Event,
    EventGroup,
    Improvement,
    Incident,
    KnowledgeArticle,
    Problem,
    ServiceRequest,
)
from serviszeka.services import kpi, sla_forecast

ROOT = Path(__file__).resolve().parents[1]
COUNTED = (
    ConfigurationItem, CIRelationship, Incident, Problem, KnowledgeArticle, ChangeRequest,
    ChangeApproval, ServiceRequest, ActionRequest, AIDecision, EventGroup, Event, Improvement,
    AuditLog,
)  # fmt: skip


async def _counts(session) -> dict[str, int]:
    return {
        m.__name__: await session.scalar(select(func.count()).select_from(m)) for m in COUNTED
    }


# --- ortam kilidi ---------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["production", "staging", "test"])
async def test_seed_refuses_outside_development(session, monkeypatch, env):
    monkeypatch.setenv("SZ_ENVIRONMENT", env)
    get_settings.cache_clear()
    with pytest.raises(demo_seed.DemoSeedError, match="SZ_ENVIRONMENT=development"):
        await demo_seed.seed(session)
    assert await session.scalar(select(func.count()).select_from(Incident)) == 0
    assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_cli_refuses_outside_development(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SZ_ENVIRONMENT", "production")
    get_settings.cache_clear()
    db = tmp_path / "demo.db"
    assert demo_seed.main(["--database-url", f"sqlite+aiosqlite:///{db}"]) == 2
    assert "SZ_ENVIRONMENT=development" in capsys.readouterr().err
    assert not db.exists()


async def test_seed_refuses_non_empty_unseeded_database(session):
    """Gerçek veri taşıyan (demo işareti olmayan) veritabanına SENTETİK veri karışmaz: hiçbir şey
    yazılmaz (denetim zincirine de)."""
    await audit.record(
        session, actor="ayse", action="incident.create", entity_type="incident", entity_id="x"
    )
    await session.commit()
    with pytest.raises(demo_seed.DemoSeedError, match="boş değil"):
        await demo_seed.seed(session)
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(Incident)) == 0
    assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_cli_refuses_non_empty_unseeded_database(tmp_path, capsys):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from serviszeka.db import Base

    db = tmp_path / "real.db"
    url = f"sqlite+aiosqlite:///{db}"

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as s:
            await audit.record(
                s, actor="ayse", action="incident.create", entity_type="incident", entity_id="x"
            )
            await s.commit()
        await engine.dispose()

    asyncio.run(prepare())
    assert demo_seed.main(["--database-url", url]) == 2
    assert "boş değil" in capsys.readouterr().err


# --- içerik ---------------------------------------------------------------------------------------


async def test_seed_creates_realistic_turkish_demo_data(session):
    summary = await demo_seed.seed(session)
    await session.commit()
    assert summary is not None and summary.created

    # CMDB: ~25 CI, bağımlılıklar (ERP, CRM, VPN, veritabanı kümesi, k8s düğümleri)
    names = set((await session.scalars(select(ConfigurationItem.name))).all())
    assert len(names) >= 25
    for expected in ("sap-erp-app", "crm-app", "vpn-gw-01", "pg-cluster-01", "k8s-node-01"):
        assert expected in names
    assert await session.scalar(select(func.count()).select_from(CIRelationship)) >= 20

    # 40 olay, son 60 güne yayılmış; çoğu çözülmüş, birkaçı açık
    incidents = (
        await session.scalars(select(Incident).where(Incident.kind == TicketKind.INCIDENT))
    ).all()
    assert len(incidents) == 40
    now = datetime.now(UTC)
    ages = [(now - inc.created_at.replace(tzinfo=UTC)).days for inc in incidents]
    assert max(ages) >= 50 and min(ages) == 0 and max(ages) <= 60
    resolved = [i for i in incidents if i.resolved_at is not None]
    assert len(resolved) >= 25
    assert any(i.sla_paused_seconds for i in incidents)
    assert any(i.status == IncidentStatus.ON_HOLD for i in incidents)
    assert any("VPN" in i.title for i in incidents)
    assert any(i.caller == "zeynep" for i in incidents)

    # Sorun / bilinen hata / KEDB ve bilgi bankası
    problems = (await session.scalars(select(Problem))).all()
    states = {p.state for p in problems}
    assert len(problems) >= 3
    assert ProblemState.KNOWN_ERROR in states and ProblemState.INVESTIGATING in states
    assert any(p.known_error_article_id for p in problems)
    assert any(p.rca_draft for p in problems)  # yapay zekâ RCA taslağı insan kabulünü bekler
    articles = (await session.scalars(select(KnowledgeArticle))).all()
    assert sum(a.state == "published" for a in articles) >= 6
    assert any(a.is_known_error for a in articles)

    # Değişiklikler: farklı durumlar, biri CAB onayı bekliyor (1/2 oy)
    changes = (await session.scalars(select(ChangeRequest))).all()
    change_states = {c.state for c in changes}
    assert {ChangeState.AUTHORIZE, ChangeState.SCHEDULED, ChangeState.CLOSED,
            ChangeState.DRAFT} <= change_states  # fmt: skip
    assert {c.change_type.value for c in changes} >= {"standard", "normal", "emergency"}
    assert any(c.implementation_outcome == "failed" for c in changes)
    waiting = [c for c in changes if c.state == ChangeState.AUTHORIZE]
    assert len(waiting) == 1
    votes = (
        await session.scalars(
            select(ChangeApproval).where(ChangeApproval.change_id == waiting[0].id)
        )
    ).all()
    assert len(votes) == 1  # ikinci CAB oyu turda verilir

    # Katalog talepleri: bir kısmı insansız karşılandı, biri iş onayı, biri eylem onayı bekliyor
    requests = (await session.scalars(select(ServiceRequest))).all()
    assert sum(bool(r.touchless) for r in requests) >= 3
    assert any(r.state == ServiceRequestState.AWAITING_APPROVAL for r in requests)
    assert any(r.waiting_action_id for r in requests)

    # Bağıntılı uyarı grubu: tek grup, kök CI veritabanı kümesi, olay açma önerisi onay bekliyor
    groups = (await session.scalars(select(EventGroup))).all()
    assert len(groups) == 1 and groups[0].state == EventGroupState.OPEN
    assert groups[0].event_count >= 2
    root = await session.get(ConfigurationItem, groups[0].root_ci_id)
    assert root is not None and root.name == "pg-cluster-01"
    pending = (
        await session.scalars(
            select(ActionRequest).where(ActionRequest.state == ActionRequestState.PENDING)
        )
    ).all()
    assert {"event.create_incident"} <= {a.tool for a in pending}

    # Yapay zekâ onay akışı: P1 triage önerisi insan onayı bekliyor
    decisions = (await session.scalars(select(AIDecision))).all()
    assert any(d.outcome == DecisionOutcome.NEEDS_APPROVAL for d in decisions)

    # Sürekli iyileştirme: ölçüm penceresi dolmuş, "Ölç" ile ölçülebilir kayıt
    improvements = (await session.scalars(select(Improvement))).all()
    assert len(improvements) == 1
    imp = improvements[0]
    assert imp.state == ImprovementState.IN_PROGRESS and imp.implemented_at is not None
    assert imp.baseline_value is not None

    assert await audit.verify_chain(session)


async def test_seed_is_idempotent(session):
    first = await demo_seed.seed(session)
    await session.commit()
    before = await _counts(session)
    second = await demo_seed.seed(session)
    await session.commit()
    assert first is not None and first.created
    assert second is not None and not second.created
    assert await _counts(session) == before


async def test_seeded_data_feeds_kpis_forecast_and_csi(session):
    await demo_seed.seed(session)
    await session.commit()
    now = datetime.now(UTC)
    report = await kpi.report(session, days=30, now=now)
    by_key = {k.key: k for k in report.kpis}
    for key in ("mttr", "mtta", "sla_resolve_compliance", "backlog", "automation_rate",
                "change_success_rate", "ai_triage_acceptance"):  # fmt: skip
        assert by_key[key].value is not None, key
    assert by_key["mttr"].previous is not None  # önceki pencerede de veri var (eğilim)

    forecast = await sla_forecast.forecast(session, now=now)
    assert any(item.probability is not None for item in forecast.items)

    from serviszeka.agents.improvement import detect_signals

    keys = {s.key for s in await detect_signals(session, now=now)}
    assert "mttr_increase:assignment_group:network_ops" in keys


# --- komut satırı ve dağıtım dosyaları ------------------------------------------------------------


def test_cli_seeds_sqlite_file_twice(tmp_path, capsys):
    db = tmp_path / "demo.db"
    url = f"sqlite+aiosqlite:///{db}"
    assert demo_seed.main(["--database-url", url]) == 0
    out = capsys.readouterr().out
    assert "zeynep" in out and "sef.ali" in out  # giriş tablosu
    assert demo_seed.main(["--database-url", url]) == 0
    assert "zaten" in capsys.readouterr().out


def test_demo_compose_file_is_valid_and_references_existing_dockerfiles():
    path = ROOT / "docker-compose.demo.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = data["services"]
    assert {"db", "app", "portal"} <= set(services)
    for name, svc in services.items():
        build = svc.get("build")
        if build is None:
            assert "image" in svc, name
            continue
        context = build if isinstance(build, str) else build.get("context", ".")
        dockerfile = "Dockerfile" if isinstance(build, str) else build.get("dockerfile", "Dockerfile")
        assert (ROOT / context / dockerfile).is_file(), f"{name}: {context}/{dockerfile}"
    env = services["app"]["environment"]
    assert env["SZ_ENVIRONMENT"] == "development"
    assert env["SZ_AUTH_MODE"] == "dev"
    assert env["SZ_LLM_PROVIDER"] == "fake"
    assert env["SZ_IDENTITY_PROVIDER"] == "memory"
    assert str(env["SZ_SCHEDULER_ENABLED"]).lower() == "true"
    assert env["SZ_DATABASE_URL"].startswith("postgresql+asyncpg://")
    command = " ".join(services["app"]["command"])
    assert "serviszeka.demo_seed" in command and "alembic upgrade head" in command
    assert services["portal"]["build"]["args"]["VITE_AUTH_MODE"] == "dev"
    # İmzasız dev kimlik doğrulaması (herkes admin olabilir): yayınlanan portlar yalnızca yerel
    # makineye bağlanır, ağdaki başka biri demo API'sine/portalına erişemez
    for name, svc in services.items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port}"


def test_demo_script_exists_and_is_executable():
    script = ROOT / "scripts" / "demo.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111
    text = script.read_text(encoding="utf-8")
    for needle in ("SZ_ENVIRONMENT=development", "serviszeka.demo_seed", "uvicorn", "npm"):
        assert needle in text


def test_demo_guide_mentions_every_demo_user():
    text = (ROOT / "DEMO.md").read_text(encoding="utf-8")
    for user in demo_seed.DEMO_USERS:
        assert f"`{user.username}`" in text


# --- PostgreSQL (isteğe bağlı: SZ_TEST_POSTGRES_URL; demo compose PostgreSQL kullanır) ------------

PG_URL = os.environ.get("SZ_TEST_POSTGRES_URL")


@pytest.mark.skipif(not PG_URL, reason="SZ_TEST_POSTGRES_URL tanımlı değil")
async def test_seed_on_postgres_is_idempotent():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from serviszeka import db

    engine = create_async_engine(PG_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
            await conn.run_sync(db.Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as pg:
            first = await demo_seed.seed(pg)
            await pg.commit()
            before = await _counts(pg)
        async with maker() as pg:
            second = await demo_seed.seed(pg)
            await pg.commit()
            assert await _counts(pg) == before
            assert await audit.verify_chain(pg)
        assert first.created and not second.created
        assert before["Incident"] == 40
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await engine.dispose()
