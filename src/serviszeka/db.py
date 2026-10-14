"""Veritabanı motoru, oturum fabrikası ve ORM taban sınıfı."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from serviszeka.config import get_settings

# Metadata'ya adlandırma kuralı UYGULANMAZ: göç öncesi create_all ile kurulmuş veritabanları
# varsayılan (PostgreSQL) ya da adsız (SQLite) kısıt adlarıyla çalışıyor; kural eklemek mevcut
# kurulumlarla yeni kurulumların şemasını ayrıştırır. SQLite'ta adsız bir kısıtı silmesi gereken
# göç bu kuralı `op.batch_alter_table(..., naming_convention=NAMING_CONVENTION)` ile kullanır.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    _engine = create_async_engine(url or get_settings().database_url)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def create_all() -> None:
    from serviszeka import models  # noqa: F401  (tabloları kaydeder)

    if _engine is None:
        init_engine()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI bağımlılığı."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    """Motoru ve havuzdaki bağlantıları kapatır (testler ve kapanış için)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
