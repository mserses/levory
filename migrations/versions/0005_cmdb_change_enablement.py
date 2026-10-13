"""Faz 2: CMDB (yapılandırma öğeleri, ilişkiler) ve değişiklik yönetimi (CAB)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13 07:29:10.806099

Yeni tablolar: configuration_items, ci_relationships, standard_change_templates,
change_requests, change_affected_cis, change_approvals, change_freeze_windows;
ai_decisions.change_id (değişiklik kararları). Enum sütunları PostgreSQL'de adlı tip değil
VARCHAR'dır (native_enum=False). Tablolar/indeksler yalnızca yoksa kurulur, sütun yalnızca yoksa
eklenir: güncel modelle `create_all` edilmiş veritabanında da `alembic upgrade head` çalışır.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from serviszeka.db import NAMING_CONVENTION

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: str | Sequence[str] | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# PostgreSQL'in create_all ile verdiği adla aynı (ai_decisions_<sütun>_fkey)
_FK_NAME = 'ai_decisions_change_id_fkey'


def _ai_decision_columns() -> set[str] | None:
    # Çevrimdışı (--sql) üretimde veritabanı yok: None = "bilinmiyor", tüm adımlar yazılır
    if context.is_offline_mode():
        return None
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('ai_decisions')}


def _change_fk_name() -> str:
    if context.is_offline_mode():
        return _FK_NAME
    for fk in sa.inspect(op.get_bind()).get_foreign_keys('ai_decisions'):
        if fk['constrained_columns'] == ['change_id']:
            return fk.get('name') or 'fk_ai_decisions_change_id_change_requests'
    return _FK_NAME


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('change_freeze_windows',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('environments', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('change_freeze_windows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_freeze_windows_end_at'), ['end_at'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_change_freeze_windows_start_at'), ['start_at'], unique=False, if_not_exists=True)

    op.create_table('configuration_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('ci_class', sa.Enum('APPLICATION', 'SERVICE', 'SERVER', 'DATABASE', 'NETWORK_DEVICE', 'CLOUD_RESOURCE', name='ciclass', native_enum=False, length=30), nullable=False),
    sa.Column('environment', sa.Enum('PROD', 'TEST', 'DEV', name='cienvironment', native_enum=False, length=30), nullable=False),
    sa.Column('criticality', sa.Integer(), nullable=False),
    sa.Column('owner_group', sa.String(length=100), nullable=True),
    sa.Column('status', sa.Enum('PLANNED', 'ACTIVE', 'MAINTENANCE', 'RETIRED', name='cistatus', native_enum=False, length=30), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('attributes', sa.JSON(), nullable=False),
    sa.Column('external_refs', sa.JSON(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'environment'),
    if_not_exists=True,
    )
    with op.batch_alter_table('configuration_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_configuration_items_ci_class'), ['ci_class'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_configuration_items_name'), ['name'], unique=False, if_not_exists=True)

    op.create_table('standard_change_templates',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('max_risk', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='risklevel', native_enum=False, length=30), nullable=False),
    sa.Column('allowed_ci_classes', sa.JSON(), nullable=False),
    sa.Column('implementation_plan', sa.Text(), nullable=False),
    sa.Column('backout_plan', sa.Text(), nullable=False),
    sa.Column('test_plan', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name'),
    if_not_exists=True,
    )
    op.create_table('change_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('change_type', sa.Enum('STANDARD', 'NORMAL', 'EMERGENCY', name='changetype', native_enum=False, length=30), nullable=False),
    sa.Column('state', sa.Enum('DRAFT', 'ASSESS', 'AUTHORIZE', 'SCHEDULED', 'IMPLEMENT', 'REVIEW', 'CLOSED', 'REJECTED', 'CANCELLED', 'FAILED', name='changestate', native_enum=False, length=30), nullable=False),
    sa.Column('risk_level', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='risklevel', native_enum=False, length=30), nullable=True),
    sa.Column('risk_score', sa.Integer(), nullable=True),
    sa.Column('implementation_plan', sa.Text(), nullable=False),
    sa.Column('backout_plan', sa.Text(), nullable=False),
    sa.Column('test_plan', sa.Text(), nullable=False),
    sa.Column('planned_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('planned_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('actual_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('actual_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested_by', sa.String(length=200), nullable=False),
    sa.Column('assignment_group', sa.String(length=100), nullable=True),
    sa.Column('assignee', sa.String(length=200), nullable=True),
    sa.Column('template_id', sa.String(length=36), nullable=True),
    sa.Column('linked_incident_ids', sa.JSON(), nullable=False),
    sa.Column('implementation_outcome', sa.String(length=20), nullable=True),
    sa.Column('review_notes', sa.Text(), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['standard_change_templates.id'], ),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_requests_number'), ['number'], unique=True, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_change_requests_requested_by'), ['requested_by'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_change_requests_state'), ['state'], unique=False, if_not_exists=True)

    op.create_table('ci_relationships',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_id', sa.String(length=36), nullable=False),
    sa.Column('target_id', sa.String(length=36), nullable=False),
    sa.Column('type', sa.Enum('DEPENDS_ON', 'RUNS_ON', 'CONNECTS_TO', 'PART_OF', name='cirelationshiptype', native_enum=False, length=30), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['configuration_items.id'], ),
    sa.ForeignKeyConstraint(['target_id'], ['configuration_items.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'target_id', 'type'),
    if_not_exists=True,
    )
    with op.batch_alter_table('ci_relationships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ci_relationships_source_id'), ['source_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_ci_relationships_target_id'), ['target_id'], unique=False, if_not_exists=True)

    op.create_table('change_affected_cis',
    sa.Column('change_id', sa.String(length=36), nullable=False),
    sa.Column('ci_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['change_id'], ['change_requests.id'], ),
    sa.ForeignKeyConstraint(['ci_id'], ['configuration_items.id'], ),
    sa.PrimaryKeyConstraint('change_id', 'ci_id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('change_affected_cis', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_affected_cis_ci_id'), ['ci_id'], unique=False, if_not_exists=True)

    op.create_table('change_approvals',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('change_id', sa.String(length=36), nullable=False),
    sa.Column('approver', sa.String(length=200), nullable=False),
    sa.Column('approver_roles', sa.JSON(), nullable=False),
    sa.Column('decision', sa.Enum('APPROVE', 'REJECT', name='approvaldecision', native_enum=False, length=30), nullable=False),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['change_id'], ['change_requests.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('change_id', 'approver'),
    if_not_exists=True,
    )
    with op.batch_alter_table('change_approvals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_approvals_change_id'), ['change_id'], unique=False, if_not_exists=True)

    existing = _ai_decision_columns()
    if existing is None or 'change_id' not in existing:
        # SQLite'ta FK eklemek tablo kopyalar (batch; canlı bağlantı gerekir)
        with op.batch_alter_table('ai_decisions', schema=None) as batch_op:
            batch_op.add_column(sa.Column('change_id', sa.String(length=36), nullable=True))
            batch_op.create_foreign_key(_FK_NAME, 'change_requests', ['change_id'], ['id'])
    op.create_index(op.f('ix_ai_decisions_change_id'), 'ai_decisions', ['change_id'], unique=False, if_not_exists=True)



def downgrade() -> None:
    """Downgrade schema."""
    existing = _ai_decision_columns()
    if existing is None or 'change_id' in existing:
        fk_name = _change_fk_name()
        # SQLite'ta create_all ile kurulan FK adsızdır: adlandırma kuralıyla bulunur
        with op.batch_alter_table(
            'ai_decisions', schema=None, naming_convention=NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_index(batch_op.f('ix_ai_decisions_change_id'))
            batch_op.drop_constraint(fk_name, type_='foreignkey')
            batch_op.drop_column('change_id')

    with op.batch_alter_table('change_approvals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_change_approvals_change_id'))

    op.drop_table('change_approvals')
    with op.batch_alter_table('change_affected_cis', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_change_affected_cis_ci_id'))

    op.drop_table('change_affected_cis')
    with op.batch_alter_table('ci_relationships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ci_relationships_target_id'))
        batch_op.drop_index(batch_op.f('ix_ci_relationships_source_id'))

    op.drop_table('ci_relationships')
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_change_requests_state'))
        batch_op.drop_index(batch_op.f('ix_change_requests_requested_by'))
        batch_op.drop_index(batch_op.f('ix_change_requests_number'))

    op.drop_table('change_requests')
    op.drop_table('standard_change_templates')
    with op.batch_alter_table('configuration_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_configuration_items_name'))
        batch_op.drop_index(batch_op.f('ix_configuration_items_ci_class'))

    op.drop_table('configuration_items')
    with op.batch_alter_table('change_freeze_windows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_change_freeze_windows_start_at'))
        batch_op.drop_index(batch_op.f('ix_change_freeze_windows_end_at'))

    op.drop_table('change_freeze_windows')
