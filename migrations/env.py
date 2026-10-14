"""Alembic ortamı: async motor, serviszeka.db.Base metadata'sı ve SZ_DATABASE_URL."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from serviszeka import models  # noqa: F401  (tabloları metadata'ya kaydeder)
from serviszeka.config import get_settings
from serviszeka.db import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    # Açıkça verilen adres (testler) önceliklidir; yoksa ayarlardan okunur
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _configure(**kw) -> None:
    url = kw.get("url") or str(kw["connection"].engine.url)
    context.configure(
        target_metadata=target_metadata,
        # SQLite ALTER TABLE kısıtları için tablo kopyalamalı (batch) değişiklik
        render_as_batch=url.startswith("sqlite"),
        compare_type=True,
        **kw,
    )


def run_migrations_offline() -> None:
    """Veritabanına bağlanmadan SQL betiği üretir (alembic upgrade --sql)."""
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
