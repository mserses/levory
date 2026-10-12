"""Ortak test fikstürleri: her test için ayrı, bellek içi SQLite veritabanı."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import db
from serviszeka.config import get_settings


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch):
    """API testleri geliştirme kipi belirteçleriyle (`dev:<kullanıcı>:<roller>`) kimlik doğrular.

    OIDC yolu tests/test_auth.py içinde gerçek RSA anahtarları ve sahte JWKS ile sınanır.
    """
    monkeypatch.setenv("SZ_AUTH_MODE", "dev")
    monkeypatch.setenv("SZ_ENVIRONMENT", "development")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    db.init_engine("sqlite+aiosqlite:///:memory:")
    await db.create_all()
    try:
        async with db.get_sessionmaker()() as s:
            yield s
    finally:
        # Bağlantılar olay döngüsü kapanmadan kapatılmalı; aksi halde aiosqlite
        # iş parçacıkları "Event loop is closed" uyarısı üretir.
        await db.dispose_engine()
