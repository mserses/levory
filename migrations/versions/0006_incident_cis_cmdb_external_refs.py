"""Olay <-> CI bağı (incident_cis) ve harici CMDB eşlemesi (cmdb_external_refs)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 07:47:28.154568

incident_cis: olaydan etkilenen yapılandırma öğeleri (triage etki kanıtı).
cmdb_external_refs: ServiceNow cmdb_ci / cmdb_rel_ci sys_id -> yerel CI/ilişki eşlemesi;
(system, entity_type, external_id) tekil, remote_updated artımlı çekim filigranı.
Tablolar/indeksler yalnızca yoksa kurulur: güncel modelle `create_all` edilmiş veritabanında da
`alembic upgrade head` çalışır.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: str | Sequence[str] | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('cmdb_external_refs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('system', sa.Enum('SERVICENOW', 'JIRA_SM', name='externalsystem', native_enum=False, length=30), nullable=False),
    sa.Column('entity_type', sa.String(length=20), nullable=False),
    sa.Column('external_id', sa.String(length=100), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=False),
    sa.Column('remote_updated', sa.String(length=64), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('system', 'entity_type', 'external_id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('cmdb_external_refs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cmdb_external_refs_entity_id'), ['entity_id'], unique=False, if_not_exists=True)

    op.create_table('incident_cis',
    sa.Column('incident_id', sa.String(length=36), nullable=False),
    sa.Column('ci_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['ci_id'], ['configuration_items.id'], ),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.PrimaryKeyConstraint('incident_id', 'ci_id'),
    if_not_exists=True,
    )
    with op.batch_alter_table('incident_cis', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_incident_cis_ci_id'), ['ci_id'], unique=False, if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('incident_cis', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_incident_cis_ci_id'))

    op.drop_table('incident_cis')
    with op.batch_alter_table('cmdb_external_refs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cmdb_external_refs_entity_id'))

    op.drop_table('cmdb_external_refs')
