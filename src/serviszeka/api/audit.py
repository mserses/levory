"""Denetim günlüğü uç noktaları."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.auth import AdminUser, get_current_user
from serviszeka.db import get_session
from serviszeka.domain.types import UTCDateTime
from serviszeka.models import AuditLog

# Denetim günlüğü yalnızca admin içindir
router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    occurred_at: UTCDateTime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    details: dict[str, Any]
    prev_hash: str
    hash: str


class AuditPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditEntryOut]


class VerifyOut(BaseModel):
    valid: bool


@router.get("", response_model=AuditPage)
async def list_audit(
    session: SessionDep,
    _user: AdminUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    total = await session.scalar(select(func.count()).select_from(AuditLog)) or 0
    rows = (
        await session.scalars(
            select(AuditLog).order_by(AuditLog.seq.desc()).limit(limit).offset(offset)
        )
    ).all()
    return AuditPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditEntryOut.model_validate(r) for r in rows],
    )


@router.get("/verify", response_model=VerifyOut)
async def verify(session: SessionDep, _user: AdminUser) -> VerifyOut:
    return VerifyOut(valid=await audit.verify_chain(session))
