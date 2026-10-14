"""Faz 3: hizmet kataloğu (catalog_items), hizmet talepleri (service_requests) ve
action_requests.request_id indeksi

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13 11:00:00.000000

catalog_items: form şeması (JSON şeması alt kümesi), iş onayı politikası (none/manager/group),
karşılama planı (sıralı araç adımları), standart bayrağı. service_requests: REQ0000001 numaralı
talep; plan kopyası, form değerleri, durum, iş onayı, adım sonuçları, insansız çözüm ölçümü
(touchless, human_approvals, human_edits, touches). action_requests.request_id FK'siz indekslenir
(SQLite'ta tablo kopyalamadan). Önceki göçlerle aynı idempotent üslup: tablo/indeks yalnızca yoksa
kurulur; güncel modelle `create_all` edilmiş veritabanında da `alembic upgrade head` çalışır. Enum
sütunları VARCHAR (native_enum=False).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: str | Sequence[str] | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = sa.Enum('NONE', 'MANAGER', 'GROUP', name='catalogapprovalpolicy', native_enum=False, length=30)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('catalog_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('form_schema', sa.JSON(), nullable=False),
    sa.Column('approval_policy', _POLICY, nullable=False),
    sa.Column('approval_group', sa.String(length=200), nullable=True),
    sa.Column('fulfilment_plan', sa.JSON(), nullable=False),
    sa.Column('standard', sa.Boolean(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('catalog_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_catalog_items_active'), ['active'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_catalog_items_category'), ['category'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_catalog_items_key'), ['key'], unique=True, if_not_exists=True)

    op.create_table('service_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('catalog_item_id', sa.String(length=36), nullable=False),
    sa.Column('item_key', sa.String(length=100), nullable=False),
    sa.Column('item_name', sa.String(length=200), nullable=False),
    sa.Column('standard', sa.Boolean(), nullable=False),
    sa.Column('plan', sa.JSON(), nullable=False),
    sa.Column('requester', sa.String(length=200), nullable=False),
    sa.Column('opened_by', sa.String(length=200), nullable=False),
    sa.Column('form_values', sa.JSON(), nullable=False),
    sa.Column('state', sa.Enum('SUBMITTED', 'AWAITING_APPROVAL', 'FULFILLING', 'FULFILLED', 'REJECTED', 'FAILED', 'CANCELLED', name='servicerequeststate', native_enum=False, length=30), nullable=False),
    sa.Column('approval_policy', _POLICY, nullable=False),
    sa.Column('approval_state', sa.Enum('NOT_REQUIRED', 'PENDING', 'APPROVED', 'REJECTED', name='businessapprovalstate', native_enum=False, length=30), nullable=False),
    sa.Column('approver', sa.String(length=200), nullable=True),
    sa.Column('approved_by', sa.String(length=200), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approval_comment', sa.Text(), nullable=False),
    sa.Column('current_step', sa.Integer(), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('step_results', sa.JSON(), nullable=False),
    sa.Column('waiting_action_id', sa.String(length=36), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('human_approvals', sa.Integer(), nullable=False),
    sa.Column('human_edits', sa.Integer(), nullable=False),
    sa.Column('touches', sa.JSON(), nullable=False),
    sa.Column('touchless', sa.Boolean(), nullable=True),
    sa.Column('touchless_reason', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fulfilled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['catalog_item_id'], ['catalog_items.id'], ),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('service_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_service_requests_approver'), ['approver'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_catalog_item_id'), ['catalog_item_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_item_key'), ['item_key'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_number'), ['number'], unique=True, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_requester'), ['requester'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_standard'), ['standard'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_state'), ['state'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_touchless'), ['touchless'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_service_requests_waiting_action_id'), ['waiting_action_id'], unique=False, if_not_exists=True)

    op.create_index(op.f('ix_action_requests_request_id'), 'action_requests', ['request_id'], unique=False, if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_action_requests_request_id'), table_name='action_requests')
    with op.batch_alter_table('service_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_service_requests_waiting_action_id'))
        batch_op.drop_index(batch_op.f('ix_service_requests_touchless'))
        batch_op.drop_index(batch_op.f('ix_service_requests_state'))
        batch_op.drop_index(batch_op.f('ix_service_requests_standard'))
        batch_op.drop_index(batch_op.f('ix_service_requests_requester'))
        batch_op.drop_index(batch_op.f('ix_service_requests_number'))
        batch_op.drop_index(batch_op.f('ix_service_requests_item_key'))
        batch_op.drop_index(batch_op.f('ix_service_requests_catalog_item_id'))
        batch_op.drop_index(batch_op.f('ix_service_requests_approver'))

    op.drop_table('service_requests')
    with op.batch_alter_table('catalog_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_catalog_items_key'))
        batch_op.drop_index(batch_op.f('ix_catalog_items_category'))
        batch_op.drop_index(batch_op.f('ix_catalog_items_active'))

    op.drop_table('catalog_items')
