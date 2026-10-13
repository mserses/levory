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


_PHASE2_TABLES = {
    "configuration_items",
    "ci_relationships",
    "standard_change_templates",
    "change_requests",
    "change_affected_cis",
    "change_approvals",
    "change_freeze_windows",
}


def test_0005_upgrade_from_0004_keeps_data_and_downgrades(alembic_cfg):
    # Faz 2 göçü: mevcut kararlar korunur (change_id NULL), tekil kısıtlar kurulur, geri alınabilir
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "0004")
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO ai_decisions (id, agent, action, input_summary, output, rationale, "
                "sources, model, confidence, outcome, created_at) VALUES ('d1', 'service_desk', "
                "'classify', '', '{}', '', '[]', 'm', 0.5, 'SUGGESTED', '2026-01-01 00:00:00')"
            )
        command.upgrade(cfg, "head")
        _assert_matches_metadata(sync_url)
        insp = inspect(engine)
        assert _PHASE2_TABLES <= set(insp.get_table_names())

        def uniques(table):
            return {tuple(u["column_names"]) for u in insp.get_unique_constraints(table)}

        assert ("change_id", "approver") in uniques("change_approvals")
        assert ("source_id", "target_id", "type") in uniques("ci_relationships")
        assert ("name", "environment") in uniques("configuration_items")
        fks = insp.get_foreign_keys("ai_decisions")
        assert any(fk["referred_table"] == "change_requests" for fk in fks)
        with engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT id, change_id FROM ai_decisions").one()
        assert tuple(row) == ("d1", None)

        command.downgrade(cfg, "0004")
        insp = inspect(engine)
        assert not _PHASE2_TABLES & set(insp.get_table_names())
        assert "change_id" not in {c["name"] for c in insp.get_columns("ai_decisions")}
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT id FROM ai_decisions").scalar_one() == "d1"
    finally:
        engine.dispose()


def test_0005_downgrade_on_create_all_database(alembic_cfg):
    # create_all ile kurulmuş SQLite'ta ai_decisions.change_id FK'si adsızdır; geri alma çökmemeli
    cfg, sync_url = alembic_cfg
    _create_all(sync_url, legacy=True)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0004")
    engine = create_engine(sync_url)
    try:
        insp = inspect(engine)
        assert "change_id" not in {c["name"] for c in insp.get_columns("ai_decisions")}
        assert not _PHASE2_TABLES & set(insp.get_table_names())
    finally:
        engine.dispose()


def test_0006_incident_cis_and_cmdb_refs_upgrade_from_0005_and_downgrade(alembic_cfg):
    # Olay <-> CI bağı ve harici CMDB eşlemesi: mevcut olaylar korunur, geri alınabilir
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "0005")
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO incidents (id, number, kind, title, description, caller, impact, "
                "urgency, priority, status, version, sla_paused_seconds, created_at, updated_at) "
                "VALUES ('i1', 'INC0000001', 'INCIDENT', 't', '', 'c', 'LOW', 'LOW', 'P5', 'NEW', "
                "1, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        command.upgrade(cfg, "head")
        _assert_matches_metadata(sync_url)
        insp = inspect(engine)
        assert {"incident_cis", "cmdb_external_refs"} <= set(insp.get_table_names())
        uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("cmdb_external_refs")}
        assert ("system", "entity_type", "external_id") in uniques
        assert {fk["referred_table"] for fk in insp.get_foreign_keys("incident_cis")} == {
            "incidents",
            "configuration_items",
        }
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT id FROM incidents").scalar_one() == "i1"

        command.downgrade(cfg, "0005")
        insp = inspect(engine)
        assert not {"incident_cis", "cmdb_external_refs"} & set(insp.get_table_names())
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT id FROM incidents").scalar_one() == "i1"
    finally:
        engine.dispose()


def test_0006_upgrade_on_create_all_database_is_idempotent(alembic_cfg):
    # create_all ile kurulmuş güncel şemada tablolar zaten var: 0006 çökmemeli, geri alınabilmeli
    cfg, sync_url = alembic_cfg
    _create_all(sync_url, legacy=True)
    command.upgrade(cfg, "head")
    _assert_matches_metadata(sync_url)
    command.downgrade(cfg, "0005")
    engine = create_engine(sync_url)
    try:
        assert not {"incident_cis", "cmdb_external_refs"} & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


FAZ3_TABLES = {
    "action_requests", "action_approvals", "tool_invocations", "catalog_items", "service_requests",
    "problems", "problem_incidents", "problem_cis", "problem_changes",
}


def test_migration_chain_is_linear_single_head(alembic_cfg):
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_cfg
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [script.get_current_head()]
    revs = list(script.walk_revisions())  # head -> base
    for rev in revs:
        assert not isinstance(rev.down_revision, tuple | list), rev.revision  # birleştirme yok
        assert len(script.get_revision(rev.revision).nextrev) <= 1, rev.revision  # dallanma yok
    assert [r.revision for r in reversed(revs)] == [f"{n:04d}" for n in range(1, len(revs) + 1)]


def test_faz3_upgrade_from_0006_keeps_data_and_downgrades(alembic_cfg):
    # Faz 2 şemasındaki (0006) veritabanı Faz 3'e yükselir: veri korunur, geri alınabilir
    cfg, sync_url = alembic_cfg
    command.upgrade(cfg, "0006")
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO incidents (id, number, kind, title, description, caller, impact, "
                "urgency, priority, status, version, sla_paused_seconds, created_at, updated_at) "
                "VALUES ('i1', 'INC0000001', 'INCIDENT', 't', '', 'c', 'LOW', 'LOW', 'P5', 'NEW', "
                "1, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        command.upgrade(cfg, "head")
        _assert_matches_metadata(sync_url)
        assert FAZ3_TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT id FROM incidents").scalar_one() == "i1"
        command.downgrade(cfg, "0006")
        assert not FAZ3_TABLES & set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT id FROM incidents").scalar_one() == "i1"
        command.upgrade(cfg, "head")
        _assert_matches_metadata(sync_url)
    finally:
        engine.dispose()
