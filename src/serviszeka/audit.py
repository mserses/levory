"""Değiştirilemez, hash zincirli denetim günlüğü.

Her kayıt, bir önceki kaydın hash'ini içerir; herhangi bir satırın sonradan
değiştirilmesi verify_chain() ile tespit edilir.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.models import AuditLog

GENESIS_HASH = "0" * 64


def _normalize_ts(ts: datetime) -> str:
    # SQLite saat dilimini düşürür: naive değerler UTC kabul edilir.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _normalize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    # JSON'a gidiş-dönüş: saklanan değer ile hash'lenen değer birebir aynı olur.
    return json.loads(json.dumps(details or {}, default=str, ensure_ascii=False))


def compute_hash(
    *,
    prev_hash: str,
    occurred_at: datetime,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, Any] | None,
) -> str:
    payload = {
        "prev_hash": prev_hash,
        "occurred_at": _normalize_ts(occurred_at),
        "actor": actor,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details or {},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_hash(row: AuditLog) -> str:
    return compute_hash(
        prev_hash=row.prev_hash,
        occurred_at=row.occurred_at,
        actor=row.actor,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        details=row.details,
    )


async def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict | None = None,
) -> AuditLog:
    """Yeni denetim kaydı ekler ve flush eder (commit etmez)."""
    last_hash = await session.scalar(
        select(AuditLog.hash).order_by(AuditLog.seq.desc()).limit(1)
    )
    prev_hash = last_hash or GENESIS_HASH
    # Mikrosaniye hassasiyeti tüm veritabanlarında korunur.
    occurred_at = datetime.now(UTC)
    norm_details = _normalize_details(details)
    entry = AuditLog(
        occurred_at=occurred_at,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=norm_details,
        prev_hash=prev_hash,
        hash=compute_hash(
            prev_hash=prev_hash,
            occurred_at=occurred_at,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=norm_details,
        ),
    )
    session.add(entry)
    await session.flush()
    return entry


async def verify_chain(session: AsyncSession) -> bool:
    """Tüm zinciri yeniden hesaplar; herhangi bir oynama varsa False döner."""
    expected_prev = GENESIS_HASH
    rows = await session.scalars(select(AuditLog).order_by(AuditLog.seq.asc()))
    for row in rows:
        if row.prev_hash != expected_prev:
            return False
        if _row_hash(row) != row.hash:
            return False
        expected_prev = row.hash
    return True
