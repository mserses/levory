"""Faz 4: izleme ve olay yönetimi (event_groups, events, event_deliveries)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13 19:30:00.000000

event_groups: CMDB etki grafiğiyle bağıntılı uyarı grubu (kök CI, en yüksek önem, durum
open/resolved/merged, grup başına tek olay `incident_id`, son not, üstlenme, susturma, çırpınma).
events: Alertmanager / genel webhook uyarısı (kaynak + parmak izi + starts_at tekil; maskeli
etiket/açıklama JSON, eşlenen CI ve kural, grup, bastırma nedeni). event_deliveries: webhook
yeniden oynatma koruması için tekil teslimat anahtarı (pencere boyunca saklanır).
Önceki göçlerle aynı idempotent üslup: tablo/indeks yalnızca yoksa kurulur. Enum sütunları VARCHAR.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: str | Sequence[str] | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEVERITY = ('CRITICAL', 'HIGH', 'WARNING', 'INFO')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('event_deliveries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('dedup_key', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedup_key'),
    if_not_exists=True,
    )
    with op.batch_alter_table('event_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_deliveries_received_at'), ['received_at'], unique=False, if_not_exists=True)

    op.create_table('event_groups',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('state', sa.Enum('OPEN', 'RESOLVED', 'MERGED', name='eventgroupstate', native_enum=False, length=20), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('root_ci_id', sa.String(length=36), nullable=True),
    sa.Column('fingerprint', sa.String(length=200), nullable=True),
    sa.Column('severity', sa.Enum(*_SEVERITY, name='eventseverity', native_enum=False, length=20), nullable=False),
    sa.Column('event_count', sa.Integer(), nullable=False),
    sa.Column('first_event_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_event_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_change_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('flapping', sa.Boolean(), nullable=False),
    sa.Column('incident_id', sa.String(length=36), nullable=True),
    sa.Column('noted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('merged_into_id', sa.String(length=36), nullable=True),
    sa.Column('acknowledged_by', sa.String(length=200), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('silenced_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('silenced_by', sa.String(length=200), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.ForeignKeyConstraint(['root_ci_id'], ['configuration_items.id'], ),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('event_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_groups_fingerprint'), ['fingerprint'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_event_groups_incident_id'), ['incident_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_event_groups_last_event_at'), ['last_event_at'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_event_groups_number'), ['number'], unique=True, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_event_groups_root_ci_id'), ['root_ci_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_event_groups_state'), ['state'], unique=False, if_not_exists=True)

    op.create_table('events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('fingerprint', sa.String(length=200), nullable=False),
    sa.Column('status', sa.Enum('FIRING', 'RESOLVED', name='eventstatus', native_enum=False, length=20), nullable=False),
    sa.Column('severity', sa.Enum(*_SEVERITY, name='eventseverity', native_enum=False, length=20), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('labels', sa.JSON(), nullable=False),
    sa.Column('annotations', sa.JSON(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ci_id', sa.String(length=36), nullable=True),
    sa.Column('ci_rule', sa.String(length=100), nullable=True),
    sa.Column('group_id', sa.String(length=36), nullable=True),
    sa.Column('suppressed', sa.String(length=20), nullable=True),
    sa.Column('received_count', sa.Integer(), nullable=False),
    sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['ci_id'], ['configuration_items.id'], ),
    sa.ForeignKeyConstraint(['group_id'], ['event_groups.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source', 'fingerprint', 'starts_at'),
    if_not_exists=True,
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_events_ci_id'), ['ci_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_events_fingerprint'), ['fingerprint'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_events_group_id'), ['group_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_events_starts_at'), ['starts_at'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_events_status'), ['status'], unique=False, if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_events_status'))
        batch_op.drop_index(batch_op.f('ix_events_starts_at'))
        batch_op.drop_index(batch_op.f('ix_events_group_id'))
        batch_op.drop_index(batch_op.f('ix_events_fingerprint'))
        batch_op.drop_index(batch_op.f('ix_events_ci_id'))

    op.drop_table('events')
    with op.batch_alter_table('event_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_groups_state'))
        batch_op.drop_index(batch_op.f('ix_event_groups_root_ci_id'))
        batch_op.drop_index(batch_op.f('ix_event_groups_number'))
        batch_op.drop_index(batch_op.f('ix_event_groups_last_event_at'))
        batch_op.drop_index(batch_op.f('ix_event_groups_incident_id'))
        batch_op.drop_index(batch_op.f('ix_event_groups_fingerprint'))

    op.drop_table('event_groups')
    with op.batch_alter_table('event_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_deliveries_received_at'))

    op.drop_table('event_deliveries')
