"""Göç 0009 (sorun yönetimi): tablolar kurulur, veri korunur, geri alınır; create_all şemasında idempotent."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from serviszeka import models  # noqa: F401  (tabloları kaydeder)
from serviszeka.db import Base

ROOT = Path(__file__).resolve().parents[1]
TABLES = {"problems", "problem_incidents", "problem_cis", "problem_changes"}


@pytest.fixture
def alembic_cfg(tmp_path) -> tuple[Config, str]:
    db_file = tmp_path / "prb.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_file}")
    cfg.attributes["configure_logger"] = False
    return cfg, f"sqlite:///{db_file}"


def _previous(cfg: Config) -> str:
    rev = ScriptDirectory.from_config(cfg).get_revision("0009")
    assert isinstance(rev.down_revision, str)
    return rev.down_revision


def test_0009_upgrade_keeps_data_and_downgrades(alembic_cfg):
    cfg, sync_url = alembic_cfg
    previous = _previous(cfg)
    command.upgrade(cfg, previous)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO incidents (id, number, kind, title, description, caller, impact, "
                "urgency, priority, status, version, sla_paused_seconds, created_at, updated_at) "
                "VALUES ('i1', 'INC0000001', 'INCIDENT', 't', '', 'c', 'LOW', 'LOW', 'P5', 'NEW', "
                "1, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        command.upgrade(cfg, "0009")
        insp = inspect(engine)
        assert TABLES <= set(insp.get_table_names())
        assert {fk["referred_table"] for fk in insp.get_foreign_keys("problem_incidents")} == {
            "problems",
            "incidents",
        }
        assert {fk["referred_table"] for fk in insp.get_foreign_keys("problems")} == {
            "knowledge_articles"
        }
        indexes = {i["name"]: i for i in insp.get_indexes("problems")}
        assert indexes["ix_problems_number"]["unique"]
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO problems (id, number, title, description, state, priority, "
                "detected_by, created_by, version, created_at, updated_at) VALUES ('p1', "
                "'PRB0000001', 't', '', 'NEW', 'P3', 'ai:problem', 'ayse', 1, "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
            conn.exec_driver_sql("INSERT INTO problem_incidents VALUES ('p1', 'i1')")
        command.downgrade(cfg, previous)
        insp = inspect(engine)
        assert not TABLES & set(insp.get_table_names())
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT id FROM incidents").scalar_one() == "i1"
    finally:
        engine.dispose()


def test_0009_upgrade_on_create_all_database_is_idempotent(alembic_cfg):
    # create_all ile kurulmuş şemada tablolar zaten var: 0009 çökmemeli, geri alınabilmeli
    cfg, sync_url = alembic_cfg
    engine = create_engine(sync_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(cfg, _previous(cfg))
        command.upgrade(cfg, "0009")
        assert TABLES <= set(inspect(engine).get_table_names())
        command.downgrade(cfg, _previous(cfg))
        assert not TABLES & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
