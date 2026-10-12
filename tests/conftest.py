"""Ortak test fikstürleri: her test için ayrı, bellek içi SQLite veritabanı."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import db


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    db.init_engine("sqlite+aiosqlite:///:memory:")
    await db.create_all()
    async with db.get_sessionmaker()() as s:
        yield s
