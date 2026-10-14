"""Faz 4: kalıcı bildirim kuyruğu (notification_outbox) ve zamanlayıcı kiraları (scheduler_leases)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13 12:00:00.000000

notification_outbox: kaydı oluşturan işlemle aynı işlemde yazılan bildirim satırı (olay, maskeli
yük, alıcı, durum pending/sent/failed/dead, deneme sayısı, sonraki deneme zamanı, gizli bilgisiz son
hata, tekil idempotency anahtarı). scheduler_leases: birden fazla kopyada her zamanlayıcı işini tek
kopyanın çalıştırması için iş başına kira satırı (lease_until karşılaştırmalı güncelleme).
Önceki göçlerle aynı idempotent üslup: tablo/indeks yalnızca yoksa kurulur. Enum sütunu VARCHAR.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: str | Sequence[str] | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('notification_outbox',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('event', sa.String(length=100), nullable=False),
    sa.Column('target', sa.String(length=50), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('actor', sa.String(length=200), nullable=False),
    sa.Column('entity_type', sa.String(length=50), nullable=False),
    sa.Column('entity_id', sa.String(length=100), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('state', sa.Enum('PENDING', 'SENT', 'FAILED', 'DEAD', name='notificationstate', native_enum=False, length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key'),
    if_not_exists=True,
    )
    with op.batch_alter_table('notification_outbox', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_outbox_entity_id'), ['entity_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_notification_outbox_event'), ['event'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_notification_outbox_next_attempt_at'), ['next_attempt_at'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_notification_outbox_state'), ['state'], unique=False, if_not_exists=True)

    op.create_table('scheduler_leases',
    sa.Column('job', sa.String(length=100), nullable=False),
    sa.Column('owner', sa.String(length=200), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=20), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('runs', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('job'),
    if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('scheduler_leases')
    with op.batch_alter_table('notification_outbox', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_outbox_state'))
        batch_op.drop_index(batch_op.f('ix_notification_outbox_next_attempt_at'))
        batch_op.drop_index(batch_op.f('ix_notification_outbox_event'))
        batch_op.drop_index(batch_op.f('ix_notification_outbox_entity_id'))

    op.drop_table('notification_outbox')
