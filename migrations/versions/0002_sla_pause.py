"""SLA duraklatma: bekleme nedeni, duraklama başlangıcı ve biriken süre

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 22:00:00

Sütunlar yalnızca yoksa eklenir: güncel modelle `create_all` edilmiş veritabanında da
`alembic upgrade head` çalışır. Mevcut satırlar için sla_paused_seconds = 0.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONHOLDREASON = postgresql.ENUM(
    'AWAITING_CALLER', 'AWAITING_VENDOR', 'AWAITING_CHANGE', 'INTERNAL',
    name='onholdreason', create_type=False,
)


def _columns() -> list[sa.Column]:
    return [
        sa.Column('on_hold_reason', _ONHOLDREASON, nullable=True),
        sa.Column('sla_paused_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sla_paused_seconds', sa.Integer(), server_default='0', nullable=False),
    ]


def _existing() -> set[str] | None:
    # Çevrimdışı (--sql) üretimde veritabanı yok: None = "bilinmiyor", tüm adımlar yazılır
    if context.is_offline_mode():
        return None
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('incidents')}


def upgrade() -> None:
    """Upgrade schema."""
    _ONHOLDREASON.create(op.get_bind(), checkfirst=True)
    existing = _existing()
    # SQLite ADD COLUMN'u doğrudan destekler (NOT NULL + varsayılan dahil); batch gerekmez
    for column in _columns():
        if existing is None or column.name not in existing:
            op.add_column('incidents', column)


def downgrade() -> None:
    """Downgrade schema."""
    existing = _existing()
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        for name in ('sla_paused_seconds', 'sla_paused_at', 'on_hold_reason'):
            if existing is None or name in existing:
                batch_op.drop_column(name)
    _ONHOLDREASON.drop(op.get_bind(), checkfirst=True)
