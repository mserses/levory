"""Faz 4 açık konular: eylem isteğinde indeksli izleme grubu bağı

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-14 10:00:00.000000

action_requests.event_group_id (+ ix_action_requests_event_group_id): olay ajanı
(`agents/events.py`) ve `services.events.pending_create_request` bir grubun bekleyen/reddedilen
`event.create_incident` isteğini JSON `args` taraması (tüm istekleri Python'a okuma) yerine
indeksli SQL ile arar. Var olan `event.*` isteklerinde sütun `args.group_id`'den doldurulur.
Önceki göçlerle aynı idempotent üslup: sütun/indeks yalnızca yoksa eklenir (güncel modelle
`create_all` edilmiş veritabanında da `alembic upgrade head` çalışır), geri alırken yalnızca varsa
düşürülür.

audit_log (entity_type, entity_id) indeksi (ix_audit_log_entity): KPI / CSI ajanının olay başına
denetim geçmişi sorguları (`kpi.not_first_contact_ids`, `incident_history`) tam tarama yapmaz.
Bildirim kuyruğundaki yeni `sending` durumu (VARCHAR, kısıtsız) göç gerektirmez.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "action_requests"
_COLUMN = "event_group_id"
_INDEX = "ix_action_requests_event_group_id"
_BATCH = 500


def _columns() -> set[str] | None:
    # Çevrimdışı (--sql) üretimde veritabanı yok: None = "bilinmiyor", tüm adımlar yazılır
    if context.is_offline_mode():
        return None
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _indexes() -> set[str] | None:
    if context.is_offline_mode():
        return None
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(_TABLE)}


def _backfill() -> None:
    """`event.*` isteklerinin `args.group_id` değerini sütuna taşır (dilimler halinde)."""
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    table = sa.table(
        _TABLE,
        sa.column("id", sa.String),
        sa.column("tool", sa.String),
        sa.column("args", sa.JSON),
        sa.column(_COLUMN, sa.String),
    )
    last = ""
    while True:
        rows = bind.execute(
            sa.select(table.c.id, table.c.args)
            .where(
                table.c.tool.like("event.%"),
                table.c[_COLUMN].is_(None),
                table.c.id > last,
            )
            .order_by(table.c.id)
            .limit(_BATCH)
        ).all()
        if not rows:
            return
        for row_id, args in rows:
            if isinstance(args, str):  # bazı sürücüler JSON'u metin döndürür
                try:
                    args = json.loads(args)
                except ValueError:
                    args = None
            group_id = args.get("group_id") if isinstance(args, dict) else None
            if isinstance(group_id, str) and group_id:
                bind.execute(
                    sa.update(table).where(table.c.id == row_id).values({_COLUMN: group_id[:36]})
                )
        last = rows[-1][0]


_AUDIT_INDEX = "ix_audit_log_entity"


def _audit_indexes() -> set[str] | None:
    if context.is_offline_mode():
        return None
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("audit_log")}


def upgrade() -> None:
    """Upgrade schema."""
    audit_indexes = _audit_indexes()
    if audit_indexes is None or _AUDIT_INDEX not in audit_indexes:
        op.create_index(_AUDIT_INDEX, "audit_log", ["entity_type", "entity_id"], unique=False)
    columns = _columns()
    if columns is None or _COLUMN not in columns:
        with op.batch_alter_table(_TABLE, schema=None) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.String(length=36), nullable=True))
    indexes = _indexes()
    if indexes is None or _INDEX not in indexes:
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=False)
    _backfill()


def downgrade() -> None:
    """Downgrade schema."""
    indexes = _indexes()
    if indexes is None or _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)
    columns = _columns()
    if columns is None or _COLUMN in columns:
        with op.batch_alter_table(_TABLE, schema=None) as batch_op:
            batch_op.drop_column(_COLUMN)
    audit_indexes = _audit_indexes()
    if audit_indexes is None or _AUDIT_INDEX in audit_indexes:
        op.drop_index(_AUDIT_INDEX, table_name="audit_log")
