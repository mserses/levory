"""AIDecision.incident_version: kararın dayandığı olay sürümü (bayat onay kontrolü)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 23:00:00

Sütun yalnızca yoksa eklenir: güncel modelle `create_all` edilmiş veritabanında da
`alembic upgrade head` çalışır. Mevcut kararlar için NULL (sürüm bilinmiyor; bu kararlar
onaylanamaz, yeniden triage gerekir).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str] | None:
    # Çevrimdışı (--sql) üretimde veritabanı yok: None = "bilinmiyor", tüm adımlar yazılır
    if context.is_offline_mode():
        return None
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("ai_decisions")}


def upgrade() -> None:
    """Upgrade schema."""
    existing = _existing()
    if existing is None or "incident_version" not in existing:
        op.add_column("ai_decisions", sa.Column("incident_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    existing = _existing()
    if existing is None or "incident_version" in existing:
        with op.batch_alter_table("ai_decisions", schema=None) as batch_op:
            batch_op.drop_column("incident_version")
