"""Faz 3: sorun yönetimi (problems, problem_incidents, problem_cis, problem_changes)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13 10:00:00.000000

problems: sorun kaydı (PRB0000001), durum (new/investigating/known_error/resolved/closed), kök neden,
RCA (kesin + taslak, JSON), geçici çözüm, KEDB makalesi bağı (knowledge_articles), sürüm.
problem_incidents / problem_cis / problem_changes: bağlı olaylar, etkilenen CI'lar ve kalıcı çözüm
değişiklikleri (RFC). Önceki göçlerle aynı idempotent üslup: tablo/indeks yalnızca yoksa kurulur;
güncel modelle `create_all` edilmiş veritabanında da `alembic upgrade head` çalışır. Enum sütunları
VARCHAR (native_enum=False).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: str | Sequence[str] | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('problems',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('state', sa.Enum('NEW', 'INVESTIGATING', 'KNOWN_ERROR', 'RESOLVED', 'CLOSED', name='problemstate', native_enum=False, length=30), nullable=False),
    sa.Column('priority', sa.Enum('P1', 'P2', 'P3', 'P4', 'P5', name='priority', native_enum=False, length=10), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('assignment_group', sa.String(length=100), nullable=True),
    sa.Column('root_cause', sa.Text(), nullable=True),
    sa.Column('rca', sa.JSON(), nullable=True),
    sa.Column('rca_draft', sa.JSON(), nullable=True),
    sa.Column('workaround', sa.Text(), nullable=True),
    sa.Column('known_error_article_id', sa.String(length=36), nullable=True),
    sa.Column('detected_by', sa.String(length=200), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['known_error_article_id'], ['knowledge_articles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('problems', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_problems_known_error_article_id'), ['known_error_article_id'], unique=False, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_problems_number'), ['number'], unique=True, if_not_exists=True)
        batch_op.create_index(batch_op.f('ix_problems_state'), ['state'], unique=False, if_not_exists=True)

    op.create_table('problem_changes',
    sa.Column('problem_id', sa.String(length=36), nullable=False),
    sa.Column('change_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['change_id'], ['change_requests.id'], ),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.PrimaryKeyConstraint('problem_id', 'change_id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('problem_changes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_problem_changes_change_id'), ['change_id'], unique=False, if_not_exists=True)

    op.create_table('problem_cis',
    sa.Column('problem_id', sa.String(length=36), nullable=False),
    sa.Column('ci_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['ci_id'], ['configuration_items.id'], ),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.PrimaryKeyConstraint('problem_id', 'ci_id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('problem_cis', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_problem_cis_ci_id'), ['ci_id'], unique=False, if_not_exists=True)

    op.create_table('problem_incidents',
    sa.Column('problem_id', sa.String(length=36), nullable=False),
    sa.Column('incident_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.PrimaryKeyConstraint('problem_id', 'incident_id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('problem_incidents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_problem_incidents_incident_id'), ['incident_id'], unique=False, if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('problem_incidents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_problem_incidents_incident_id'))

    op.drop_table('problem_incidents')
    with op.batch_alter_table('problem_cis', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_problem_cis_ci_id'))

    op.drop_table('problem_cis')
    with op.batch_alter_table('problem_changes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_problem_changes_change_id'))

    op.drop_table('problem_changes')
    with op.batch_alter_table('problems', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_problems_state'))
        batch_op.drop_index(batch_op.f('ix_problems_number'))
        batch_op.drop_index(batch_op.f('ix_problems_known_error_article_id'))

    op.drop_table('problems')
