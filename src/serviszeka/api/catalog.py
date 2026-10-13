"""Hizmet kataloğu (Faz 3): her kimliği doğrulanmış kullanıcı etkin öğeleri görür; yönetim admin."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import ROLE_ADMIN, AdminUser, CurrentUserDep, get_current_user
from serviszeka.db import get_session
from serviszeka.services import catalog as svc
from serviszeka.services.request_schemas import CatalogItemRead

router = APIRouter(prefix="/catalog", tags=["catalog"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CatalogItemPatchBody(svc.CatalogItemPatch):
    expected_version: int | None = Field(default=None, ge=1)


class SeedResult(BaseModel):
    created: list[str]


def _unprocessable(exc: svc.CatalogError) -> HTTPException:
    detail = {"message": str(exc), "errors": exc.errors} if exc.errors else str(exc)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


@router.get("/items", response_model=list[CatalogItemRead])
async def list_items(
    session: SessionDep,
    user: CurrentUserDep,
    category: Annotated[str | None, Query(max_length=100)] = None,
    standard: bool | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    include_inactive: Annotated[bool, Query(description="Etkin olmayanlar da (yalnızca admin)")] = False,
) -> list[CatalogItemRead]:
    """Katalog öğeleri (kategori, ad). Etkin olmayan öğeleri yalnızca admin görebilir."""
    rows = await svc.list_items(
        session,
        category=category,
        standard=standard,
        q=q,
        include_inactive=include_inactive and ROLE_ADMIN in user.roles,
    )
    return [CatalogItemRead.model_validate(r) for r in rows]


@router.get("/items/{item}", response_model=CatalogItemRead)
async def get_item(item: str, session: SessionDep, user: CurrentUserDep) -> CatalogItemRead:
    row = await svc.get_item(session, item)
    if row is None or (not row.active and ROLE_ADMIN not in user.roles):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Katalog öğesi bulunamadı")
    return CatalogItemRead.model_validate(row)


@router.post("/items", response_model=CatalogItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    body: svc.CatalogItemInput, session: SessionDep, user: AdminUser
) -> CatalogItemRead:
    try:
        row = await svc.create_item(session, body, user.username)
    except svc.CatalogError as exc:
        raise _unprocessable(exc) from exc
    await session.commit()
    return CatalogItemRead.model_validate(row)


@router.patch("/items/{item}", response_model=CatalogItemRead)
async def update_item(
    item: str, body: CatalogItemPatchBody, session: SessionDep, user: AdminUser
) -> CatalogItemRead:
    row = await svc.get_item(session, item)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Katalog öğesi bulunamadı")
    patch = svc.CatalogItemPatch(**body.model_dump(exclude_unset=True, exclude={"expected_version"}))
    try:
        row = await svc.update_item(
            session, row, patch, user.username, expected_version=body.expected_version
        )
    except svc.CatalogError as exc:
        raise _unprocessable(exc) from exc
    except svc.CatalogVersionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await session.refresh(row)
    return CatalogItemRead.model_validate(row)


@router.post("/seed", response_model=SeedResult)
async def seed(session: SessionDep, user: AdminUser) -> SeedResult:
    """Varsayılan 10 katalog öğesini yoksa ekler (var olanlara dokunmaz)."""
    created = await svc.seed_default_items(session, user.username)
    await session.commit()
    return SeedResult(created=created)
