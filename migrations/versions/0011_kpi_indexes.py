"""Faz 4: KPI pencere sorguları için indeksler

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13 21:00:00.000000

KPI servisi (`services/kpi.py`) çözülme/yanıt zamanı ve denetim kaydı zamanı penceresiyle süzer:
incidents.resolved_at, incidents.responded_at, audit_log.occurred_at. 0004 ile aynı idempotent
üslup: indeks yalnızca yoksa kurulur (güncel modelle `create_all` edilmiş veritabanında da
`alembic upgrade head` çalışır), geri alırken yalnızca varsa düşürülür.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_incidents_resolved_at", "incidents", "resolved_at"),
    ("ix_incidents_responded_at", "incidents", "responded_at"),
    ("ix_audit_log_occurred_at", "audit_log", "occurred_at"),
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
