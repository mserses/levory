"""Alembic göçleri: boş SQLite dosyasında head'e yükseltilen şema ORM metadata'sıyla aynı olmalı."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, PrimaryKeyConstraint, UniqueConstraint, create_engine, inspect

from serviszeka import models  # noqa: F401  (tabloları kaydeder)
from serviszeka.db import Base

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_cfg(tmp_path) -> tuple[Config, str]:
    db_file = tmp_path / "migr.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_file}")
    cfg.attributes["configure_logger"] = False  # pytest log yakalamasını bozmasın
    return cfg, f"sqlite:///{db_file}"


def test_upgrade_head_matches_metadata(alembic_cfg):
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "head")

    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names()) - {"alembic_version"}
        assert tables == set(Base.metadata.tables)
        for name, table in Base.metadata.tables.items():
            cols = {c["name"]: c for c in insp.get_columns(name)}
            assert set(cols) == {c.name for c in table.columns}, name
            for col in table.columns:
                assert cols[col.name]["nullable"] == col.nullable, f"{name}.{col.name}"
        # Denetim zinciri çatallanamaz: prev_hash tekil olmalı
        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("audit_log")}
        assert ("prev_hash",) in uniques and ("hash",) in uniques
        ext = {tuple(u["column_names"]) for u in insp.get_unique_constraints("external_links")}
        assert ("system", "external_id") in ext

        # Otomatik üretim farkı yok (alembic check ile aynı karşılaştırma)
        with engine.connect() as conn:
            diff = compare_metadata(
                MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
            )
        assert diff == []
    finally:
        engine.dispose()


def test_downgrade_base_drops_everything(alembic_cfg):
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(sync_url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()


@pytest.mark.parametrize("auto", [True, False])
async def test_startup_create_all_follows_setting(monkeypatch, auto):
    from serviszeka import main
    from serviszeka.config import Settings

    calls: list[int] = []

    async def _spy() -> None:
        calls.append(1)

    monkeypatch.setattr(main, "create_all", _spy)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(auto_create_schema=auto))
    async with main.lifespan(main.app):
        pass
    # Üretimde şema yalnızca `alembic upgrade head` ile kurulur
    assert calls == ([1] if auto else [])


def test_auto_create_schema_defaults_true(monkeypatch):
    from serviszeka.config import Settings

    monkeypatch.delenv("SZ_AUTO_CREATE_SCHEMA", raising=False)
    assert Settings(_env_file=None).auto_create_schema is True
    monkeypatch.setenv("SZ_AUTO_CREATE_SCHEMA", "false")
    assert Settings(_env_file=None).auto_create_schema is False


def _assert_matches_metadata(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(
                MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
            )
        assert diff == []
    finally:
        engine.dispose()


def _create_all(sync_url: str, drop_columns: tuple[str, ...] = (), legacy: bool = False) -> None:
    engine = create_engine(sync_url)
    try:
        metadata = Base.metadata
        if legacy:
            # Göç öncesi sürümün create_all'u: kısıtlar adsız (veritabanı varsayılan adları)
            metadata = MetaData()
            for table in Base.metadata.sorted_tables:
                copy = table.to_metadata(metadata)
                for constraint in copy.constraints:
                    if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint):
                        constraint.name = None
                for fk in copy.foreign_key_constraints:
                    fk.name = None
        metadata.create_all(engine)
        with engine.begin() as conn:
            for col in drop_columns:
                conn.exec_driver_sql(f"ALTER TABLE incidents DROP COLUMN {col}")
            conn.exec_driver_sql(
                "INSERT INTO incidents (id, number, kind, title, description, caller, impact, "
                "urgency, priority, status, version, created_at, updated_at) VALUES ('i1', "
                "'INC0000001', 'INCIDENT', 't', '', 'c', 'LOW', 'LOW', 'P5', 'NEW', 1, "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
    finally:
        engine.dispose()


def test_upgrade_existing_pre_migration_database(alembic_cfg):
    # Göçlerden önce create_all ile kurulmuş (SLA duraklatma sütunları olmayan) mevcut
    # veritabanı: `alembic upgrade head` çökmemeli, eksik sütunları eklemeli, veri korunmalı
    cfg, sync_url = alembic_cfg
    _create_all(
        sync_url,
        drop_columns=("on_hold_reason", "sla_paused_at", "sla_paused_seconds"),
        legacy=True,
    )
    command.upgrade(cfg, "head")
    _assert_matches_metadata(sync_url)
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT number, sla_paused_seconds, on_hold_reason FROM incidents"
            ).one()
        assert tuple(row) == ("INC0000001", 0, None)
    finally:
        engine.dispose()


def test_upgrade_database_built_by_current_create_all(alembic_cfg):
    # Geliştirmede SZ_AUTO_CREATE_SCHEMA=true ile kurulmuş güncel şema da yükseltilebilmeli
    cfg, sync_url = alembic_cfg
    _create_all(sync_url)
    command.upgrade(cfg, "head")
    _assert_matches_metadata(sync_url)


def test_upgrade_adds_decision_incident_version_to_existing_database(alembic_cfg):
    # 0003 öncesi veritabanı (ai_decisions.incident_version yok): eski kararlar NULL kalır
    cfg, sync_url = alembic_cfg
    _create_all(sync_url)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE ai_decisions DROP COLUMN incident_version")
            conn.exec_driver_sql(
                "INSERT INTO ai_decisions (id, incident_id, agent, action, input_summary, "
                "output, rationale, sources, model, confidence, outcome, created_at) VALUES "
                "('d1', 'i1', 'service_desk', 'classify', '', '{}', '', '[]', 'm', 0.5, "
                "'SUGGESTED', '2026-01-01 00:00:00')"
            )
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")
    _assert_matches_metadata(sync_url)
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT id, incident_version FROM ai_decisions").one()
        assert tuple(row) == ("d1", None)
    finally:
        engine.dispose()
