"""Faz 4: sürekli iyileştirme kaydı (improvements)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-13 21:00:00.000000

improvements: ITIL 4 CSI kaydı (CSI0000001): kaynak (kpi/problem/pir/manual/ai), durum
idea/approved/in_progress/measured/closed/rejected, bağlı KPI + kırılım, taban (pencereyle), hedef,
ölçülen değer + hedefe ulaşıldı mı + önce/sonra ölçüm JSON'u, sahip, fayda/efor/öncelik puanı,
sorun/değişiklik bağı, sinyal kanıtı ve tekilleştirme anahtarı, onay/başlatma/uygulama/kapanış
izleri, sürüm. Önceki göçlerle aynı idempotent üslup: tablo/indeks yalnızca yoksa kurulur. Enum
sütunları VARCHAR.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: str | Sequence[str] | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE = ('KPI', 'PROBLEM', 'PIR', 'MANUAL', 'AI')
_STATE = ('IDEA', 'APPROVED', 'IN_PROGRESS', 'MEASURED', 'CLOSED', 'REJECTED')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('improvements',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('source', sa.Enum(*_SOURCE, name='improvementsource', native_enum=False, length=20), nullable=False),
    sa.Column('state', sa.Enum(*_STATE, name='improvementstate', native_enum=False, length=20), nullable=False),
    sa.Column('kpi_key', sa.String(length=64), nullable=True),
    sa.Column('kpi_dimension', sa.String(length=50), nullable=True),
    sa.Column('kpi_dimension_value', sa.String(length=100), nullable=True),
    sa.Column('baseline_value', sa.Float(), nullable=True),
    sa.Column('baseline_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('baseline_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('target_value', sa.Float(), nullable=True),
    sa.Column('measured_value', sa.Float(), nullable=True),
    sa.Column('measured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('target_met', sa.Boolean(), nullable=True),
    sa.Column('measurement', sa.JSON(), nullable=True),
    sa.Column('measure_days', sa.Integer(), nullable=False),
    sa.Column('owner', sa.String(length=200), nullable=True),
    sa.Column('benefit_estimate', sa.Integer(), nullable=False),
    sa.Column('effort', sa.Integer(), nullable=False),
    sa.Column('priority_score', sa.Float(), nullable=False),
    sa.Column('problem_id', sa.String(length=36), nullable=True),
    sa.Column('change_id', sa.String(length=36), nullable=True),
    sa.Column('evidence', sa.JSON(), nullable=True),
    sa.Column('signal_key', sa.String(length=200), nullable=True),
    sa.Column('detected_by', sa.String(length=200), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=False),
    sa.Column('approved_by', sa.String(length=200), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('implemented_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_by', sa.String(length=200), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['change_id'], ['change_requests.id'], ),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('improvements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_improvements_change_id'), ['change_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_improvements_kpi_key'), ['kpi_key'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_improvements_number'), ['number'], unique=True, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_improvements_owner'), ['owner'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_improvements_problem_id'), ['problem_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_improvements_signal_key'), ['signal_key'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_improvements_source'), ['source'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_improvements_state'), ['state'], unique=False, if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('improvements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_improvements_state'))
        batch_op.drop_index(batch_op.f('ix_improvements_source'))
        batch_op.drop_index(batch_op.f('ix_improvements_signal_key'))
        batch_op.drop_index(batch_op.f('ix_improvements_problem_id'))
        batch_op.drop_index(batch_op.f('ix_improvements_owner'))
        batch_op.drop_index(batch_op.f('ix_improvements_number'))
        batch_op.drop_index(batch_op.f('ix_improvements_kpi_key'))
        batch_op.drop_index(batch_op.f('ix_improvements_change_id'))

    op.drop_table('improvements')
