"""Faz 3: eylem istekleri (action_requests, action_approvals), araç idempotency kaydı
(tool_invocations) ve ai_decisions.action_request_id

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13 09:00:00.000000

action_requests: insan onayı bekleyen araç çağrısı (R2/R3 ya da politikanın otomatik uygulamadığı
R1); ham argüman + maskeli gösterim kopyası, risk sınıfı, durum, süre sonu, bağlı olay/değişiklik.
action_approvals: (istek, approver_key) tekil — aynı kişi (büyük/küçük harf duyarsız) iki kez
sayılamaz. tool_invocations: (actor, idempotency_key) tekil. ai_decisions.action_request_id FK'siz
indeksli sütundur (SQLite'ta tablo kopyalamadan eklenir). Tablo/indeks yalnızca yoksa kurulur, sütun
yalnızca yoksa eklenir: güncel modelle `create_all` edilmiş veritabanında da `alembic upgrade head`
çalışır. Enum sütunları VARCHAR (native_enum=False).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: str | Sequence[str] | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ai_decision_columns() -> set[str] | None:
    # Çevrimdışı (--sql) üretimde veritabanı yok: None = "bilinmiyor", tüm adımlar yazılır
    if context.is_offline_mode():
        return None
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('ai_decisions')}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('action_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('tool', sa.String(length=100), nullable=False),
    sa.Column('args', sa.JSON(), nullable=False),
    sa.Column('args_display', sa.JSON(), nullable=False),
    sa.Column('args_hash', sa.String(length=64), nullable=False),
    sa.Column('risk_class', sa.Enum('R0', 'R1', 'R2', 'R3', name='riskclass', native_enum=False, length=10), nullable=False),
    sa.Column('state', sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED', 'FAILED', 'EXPIRED', name='actionrequeststate', native_enum=False, length=30), nullable=False),
    sa.Column('required_approvals', sa.Integer(), nullable=False),
    sa.Column('requested_by', sa.String(length=200), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('incident_id', sa.String(length=36), nullable=True),
    sa.Column('change_id', sa.String(length=36), nullable=True),
    sa.Column('request_id', sa.String(length=36), nullable=True),
    sa.Column('decision_id', sa.String(length=36), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('executed_by', sa.String(length=200), nullable=True),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['change_id'], ['change_requests.id'], ),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('action_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_action_requests_change_id'), ['change_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_action_requests_expires_at'), ['expires_at'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_action_requests_incident_id'), ['incident_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_action_requests_number'), ['number'], unique=True, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_action_requests_requested_by'), ['requested_by'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_action_requests_state'), ['state'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_action_requests_tool'), ['tool'], unique=False, if_not_exists=True)

    op.create_table('action_approvals',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('action_request_id', sa.String(length=36), nullable=False),
    sa.Column('approver', sa.String(length=200), nullable=False),
    sa.Column('approver_key', sa.String(length=200), nullable=False),
    sa.Column('approver_roles', sa.JSON(), nullable=False),
    sa.Column('decision', sa.Enum('APPROVE', 'REJECT', name='approvaldecision', native_enum=False, length=30), nullable=False),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['action_request_id'], ['action_requests.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('action_request_id', 'approver_key'),
    if_not_exists=True,
    )
    with op.batch_alter_table('action_approvals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_action_approvals_action_request_id'), ['action_request_id'], unique=False, if_not_exists=True)

    op.create_table('tool_invocations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('actor', sa.String(length=200), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('tool', sa.String(length=100), nullable=False),
    sa.Column('args_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('action_request_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('actor', 'idempotency_key'),
    if_not_exists=True,
    )

    existing = _ai_decision_columns()
    if existing is None or 'action_request_id' not in existing:
        op.add_column('ai_decisions', sa.Column('action_request_id', sa.String(length=36), nullable=True))
    op.create_index(op.f('ix_ai_decisions_action_request_id'), 'ai_decisions', ['action_request_id'], unique=False, if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ai_decisions_action_request_id'), table_name='ai_decisions')
    with op.batch_alter_table('ai_decisions', schema=None) as batch_op:
        batch_op.drop_column('action_request_id')

    op.drop_table('tool_invocations')
    with op.batch_alter_table('action_approvals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_action_approvals_action_request_id'))

    op.drop_table('action_approvals')
    with op.batch_alter_table('action_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_action_requests_tool'))
        batch_op.drop_index(batch_op.f('ix_action_requests_state'))
        batch_op.drop_index(batch_op.f('ix_action_requests_requested_by'))
        batch_op.drop_index(batch_op.f('ix_action_requests_number'))
        batch_op.drop_index(batch_op.f('ix_action_requests_incident_id'))
        batch_op.drop_index(batch_op.f('ix_action_requests_expires_at'))
        batch_op.drop_index(batch_op.f('ix_action_requests_change_id'))

    op.drop_table('action_requests')
