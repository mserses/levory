"""Portal sorguları için indeksler: incidents.caller, ai_decisions.outcome

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 23:30:00

Self-servis "kayıtlarım" (caller süzgeci) ve olaylar arası onay kutusu (outcome süzgeci).
İndeksler yalnızca yoksa oluşturulur: güncel modelle `create_all` edilmiş veritabanında da
`alembic upgrade head` çalışır.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_incidents_caller", "incidents", "caller"),
    ("ix_ai_decisions_outcome", "ai_decisions", "outcome"),
)


def _existing(table: str) -> set[str] | None:
    # Çevrimdışı (--sql) üretimde veritabanı yok: None = "bilinmiyor", tüm adımlar yazılır
    if context.is_offline_mode():
        return None
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    """Upgrade schema."""
    for name, table, column in _INDEXES:
        existing = _existing(table)
        if existing is None or name not in existing:
            op.create_index(name, table, [column], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    for name, table, _column in _INDEXES:
        existing = _existing(table)
        if existing is None or name in existing:
            op.drop_index(name, table_name=table)
