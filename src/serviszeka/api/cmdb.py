"""CMDB uç noktaları: yapılandırma öğeleri, ilişkiler ve etki analizi.

Okuma: agent / cab / change_manager; yazma: change_manager (admin her şeyi kapsar).
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import CMDBReaderUser, CMDBWriterUser, get_current_user
from serviszeka.db import get_session
from serviszeka.domain.enums import CIClass, CIEnvironment, CIStatus
from serviszeka.services import cmdb as svc
from serviszeka.services.cmdb_schemas import (
    CICreate,
    CIPatch,
    CIRead,
    CIRelationshipCreate,
    CIRelationshipRead,
    ImpactedCIRead,
    ImpactRead,
    ImpactRequest,
)

router = APIRouter(prefix="/cmdb", tags=["cmdb"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_ETAG_RE = re.compile(r'^\s*(?:W/)?"?(\d+)"?\s*$')


def etag(version: int) -> str:
    return f'"{version}"'


def parse_if_match(value: str | None) -> int | None:
    if value is None or value.strip() in ("", "*"):
        return None
    m = _ETAG_RE.match(value)
    if not m:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'If-Match başlığı geçersiz (örn. "3").')
    return int(m.group(1))


def _raise(exc: svc.CMDBError) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, (svc.VersionConflictError, svc.DuplicateRelationshipError)):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _impact_read(result: svc.ImpactAnalysis) -> ImpactRead:
    return ImpactRead(
        root_ci_ids=result.root_ci_ids,
        affected=[
            ImpactedCIRead(
                ci=CIRead.model_validate(a.ci),
                depth=a.depth,
                path=a.path,
                relationship=a.relationship,
            )
            for a in result.affected
        ],
        highest_criticality=result.highest_criticality,
        business_services=[CIRead.model_validate(c) for c in result.business_services],
    )


async def impact_for(session: AsyncSession, ci_ids: list[str], max_depth: int = 5) -> ImpactRead:
    """Etki analizi + HTTP hata eşlemesi (değişiklik uçları da kullanır)."""
    try:
        return _impact_read(await svc.impact_analysis(session, ci_ids, max_depth))
    except svc.CMDBError as exc:
        _raise(exc)
        raise  # pragma: no cover


@router.get("/cis", response_model=list[CIRead])
async def list_cis(
    session: SessionDep,
    _user: CMDBReaderUser,
    ci_class: CIClass | None = None,
    environment: CIEnvironment | None = None,
    status_: Annotated[CIStatus | None, Query(alias="status")] = None,
    owner_group: str | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Ad içinde arama")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CIRead]:
    rows = await svc.list_cis(
        session,
        ci_class=ci_class,
        environment=environment,
        status=status_,
        owner_group=owner_group,
        q=q,
        limit=limit,
        offset=offset,
    )
    return [CIRead.model_validate(r) for r in rows]


@router.post("/cis", response_model=CIRead, status_code=status.HTTP_201_CREATED)
async def create_ci(
    body: CICreate, session: SessionDep, response: Response, user: CMDBWriterUser
) -> CIRead:
    try:
        ci = await svc.create_ci(session, body, user.username)
    except svc.CMDBError as exc:
        _raise(exc)
    await session.commit()
    response.headers["ETag"] = etag(ci.version)
    return CIRead.model_validate(ci)


@router.post("/impact", response_model=ImpactRead)
async def impact(body: ImpactRequest, session: SessionDep, _user: CMDBReaderUser) -> ImpactRead:
    """Verilen CI'lar değişirse kim etkilenir (yukarı yönlü bağımlılar, döngü güvenli)."""
    return await impact_for(session, body.ci_ids, body.max_depth)


@router.get("/cis/{ci_id}", response_model=CIRead)
async def get_ci(
    ci_id: str, session: SessionDep, response: Response, _user: CMDBReaderUser
) -> CIRead:
    ci = await svc.get_ci(session, ci_id)
    if ci is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Yapılandırma öğesi bulunamadı")
    response.headers["ETag"] = etag(ci.version)
    return CIRead.model_validate(ci)


@router.patch("/cis/{ci_id}", response_model=CIRead)
async def update_ci(
    ci_id: str,
    body: CIPatch,
    session: SessionDep,
    response: Response,
    user: CMDBWriterUser,
    if_match: Annotated[str | None, Header()] = None,
) -> CIRead:
    expected = parse_if_match(if_match)
    if expected is None:
        expected = body.expected_version
    try:
        ci = await svc.update_ci(session, ci_id, body, user.username, expected_version=expected)
    except svc.CMDBError as exc:
        _raise(exc)
    await session.commit()
    response.headers["ETag"] = etag(ci.version)
    return CIRead.model_validate(ci)


@router.get("/cis/{ci_id}/relationships", response_model=list[CIRelationshipRead])
async def list_relationships(
    ci_id: str, session: SessionDep, _user: CMDBReaderUser
) -> list[CIRelationshipRead]:
    try:
        rows = await svc.list_relationships(session, ci_id)
    except svc.CMDBError as exc:
        _raise(exc)
    return [CIRelationshipRead.model_validate(r) for r in rows]


@router.get("/cis/{ci_id}/impact", response_model=ImpactRead)
async def ci_impact(
    ci_id: str,
    session: SessionDep,
    _user: CMDBReaderUser,
    max_depth: Annotated[int, Query(ge=1, le=svc.MAX_IMPACT_DEPTH)] = 5,
) -> ImpactRead:
    return await impact_for(session, [ci_id], max_depth)


@router.post(
    "/relationships", response_model=CIRelationshipRead, status_code=status.HTTP_201_CREATED
)
async def create_relationship(
    body: CIRelationshipCreate, session: SessionDep, user: CMDBWriterUser
) -> CIRelationshipRead:
    try:
        rel = await svc.add_relationship(
            session, body.source_id, body.target_id, body.type, user.username
        )
    except svc.CMDBError as exc:
        _raise(exc)
    await session.commit()
    return CIRelationshipRead.model_validate(rel)


@router.delete("/relationships/{rel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_relationship(rel_id: str, session: SessionDep, user: CMDBWriterUser) -> Response:
    try:
        await svc.remove_relationship(session, rel_id, user.username)
    except svc.CMDBError as exc:
        _raise(exc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
