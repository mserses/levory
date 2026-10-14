"""Sürekli iyileştirme (ITIL 4 CSI kaydı) uç noktaları.

Okuma, açma, düzenleme, başlatma, uygulandı işareti ve ölçüm: agent / approver / admin
(`ReaderUser`). Onay, red ve kapatma: approver / admin (`ApproverUser`). requester ve yapay zekâ
belirteci (`ai_agent`) 403. Servis ayrıca `ai:` aktörün onay/red/başlatma/kapatma yapmasını reddeder.
`/improvements/review` CSI ajanını `ai:csi` aktörüyle Faz 3 araç boru hattından çalıştırır
(politika izin vermezse eylem isteği açılır; onay `/actions`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import llm as llm_module
from serviszeka.agents import improvement as csi_agent
from serviszeka.api.cmdb import etag, parse_if_match
from serviszeka.auth import ApproverUser, ReaderUser, get_current_user
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import ImprovementSource, ImprovementState
from serviszeka.models import Improvement
from serviszeka.services import improvements as svc
from serviszeka.services.improvement_schemas import (
    ImplementedRequest,
    ImprovementCreate,
    ImprovementNote,
    ImprovementRead,
    ImprovementUpdate,
    KPIKey,
    ReviewRead,
    SignalsRead,
    SignalsRequest,
)
from serviszeka.tools import ToolError

router = APIRouter(
    prefix="/improvements", tags=["improvements"], dependencies=[Depends(get_current_user)]
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_csi_settings() -> Settings:
    """Ayar bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_csi_settings)]


def get_csi_llm() -> llm_module.LLMClient | None:
    """LLM bağımlılığı; yapılandırılmamışsa None (deterministik gerekçe yazılır)."""
    try:
        return llm_module.get_llm()
    except llm_module.LLMError:
        return None


LLMDep = Annotated[llm_module.LLMClient | None, Depends(get_csi_llm)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ToolError):
        return HTTPException(exc.status_code, exc.message)
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, svc.ImprovementVersionConflictError | svc.ImprovementNotReadyError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


_ERRORS = (svc.ImprovementError, ToolError)


def _read(imp: Improvement, response: Response | None = None) -> ImprovementRead:
    if response is not None:
        response.headers["ETag"] = etag(imp.version)
    return ImprovementRead.model_validate(imp)


def _expected(if_match: str | None, body_version: int | None = None) -> int | None:
    expected = parse_if_match(if_match)
    return body_version if expected is None else expected


# --- liste / sinyaller / gözden geçirme (/{id}'den önce) ----------------------------------------


@router.get("", response_model=list[ImprovementRead])
async def list_improvements(
    session: SessionDep,
    _user: ReaderUser,
    state: ImprovementState | None = None,
    source: ImprovementSource | None = None,
    kpi_key: KPIKey | None = None,
    owner: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    open_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ImprovementRead]:
    rows = await svc.list_improvements(
        session, state=state, source=source, kpi_key=kpi_key, owner=owner, q=q,
        open_only=open_only, limit=limit, offset=offset,
    )  # fmt: skip
    return [ImprovementRead.model_validate(r) for r in rows]


@router.post("", response_model=ImprovementRead, status_code=status.HTTP_201_CREATED)
async def create_improvement(
    body: ImprovementCreate,
    session: SessionDep,
    response: Response,
    settings: SettingsDep,
    user: ReaderUser,
) -> ImprovementRead:
    try:
        imp = await svc.create_improvement(session, body, user, settings=settings)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return _read(imp, response)


@router.post("/signals", response_model=SignalsRead)
async def detect_signals(
    body: SignalsRequest, session: SessionDep, _user: ReaderUser
) -> SignalsRead:
    """Deterministik iyileştirme sinyalleri (salt okuma, hiçbir şey yazmaz)."""
    signals = await csi_agent.detect_signals(session, window_days=body.window_days)
    return SignalsRead(window_days=body.window_days, signals=[s.to_read() for s in signals])


@router.post("/review", response_model=ReviewRead)
async def review(
    session: SessionDep, settings: SettingsDep, llm: LLMDep, _user: ReaderUser
) -> ReviewRead:
    """CSI ajanını şimdi çalıştırır (`ai:csi`; zamanlayıcı işi `csi_review` ile aynı)."""
    try:
        result = await csi_agent.run_review(session, llm=llm, settings=settings)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return ReviewRead(**result)


# --- tek kayıt ---------------------------------------------------------------------------------


@router.get("/{improvement_id}", response_model=ImprovementRead)
async def get_improvement(
    improvement_id: str, session: SessionDep, response: Response, _user: ReaderUser
) -> ImprovementRead:
    imp = await svc.get_improvement(session, improvement_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "İyileştirme bulunamadı")
    return _read(imp, response)


@router.patch("/{improvement_id}", response_model=ImprovementRead)
async def update_improvement(
    improvement_id: str,
    body: ImprovementUpdate,
    session: SessionDep,
    response: Response,
    user: ReaderUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ImprovementRead:
    try:
        imp = await svc.update_improvement(
            session, improvement_id, body, user,
            expected_version=_expected(if_match, body.expected_version),
        )  # fmt: skip
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return _read(imp, response)


async def _step(session, response, fn, improvement_id, user, if_match, body, **kw):
    try:
        imp = await fn(
            session, improvement_id, user,
            expected_version=_expected(if_match, body.expected_version if body else None), **kw,
        )  # fmt: skip
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return _read(imp, response)


@router.post("/{improvement_id}/approve", response_model=ImprovementRead)
async def approve(
    improvement_id: str,
    body: ImprovementNote,
    session: SessionDep,
    response: Response,
    user: ApproverUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ImprovementRead:
    """Fikri onaylar (approver / admin)."""
    return await _step(
        session, response, svc.approve, improvement_id, user, if_match, body, note=body.note
    )


@router.post("/{improvement_id}/reject", response_model=ImprovementRead)
async def reject(
    improvement_id: str,
    body: ImprovementNote,
    session: SessionDep,
    response: Response,
    user: ApproverUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ImprovementRead:
    """Reddeder / iptal eder; gerekçe zorunlu (approver / admin)."""
    return await _step(
        session, response, svc.reject, improvement_id, user, if_match, body, note=body.note
    )


@router.post("/{improvement_id}/start", response_model=ImprovementRead)
async def start(
    improvement_id: str,
    body: ImprovementNote,
    session: SessionDep,
    response: Response,
    user: ReaderUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ImprovementRead:
    """Onaylı iyileştirmenin uygulamasını başlatır."""
    return await _step(
        session, response, svc.start, improvement_id, user, if_match, body, note=body.note
    )


@router.post("/{improvement_id}/implemented", response_model=ImprovementRead)
async def implemented(
    improvement_id: str,
    body: ImplementedRequest,
    session: SessionDep,
    response: Response,
    user: ReaderUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ImprovementRead:
    """Uygulamanın bittiği anı kaydeder (ölçüm penceresi buradan başlar)."""
    return await _step(
        session, response, svc.mark_implemented, improvement_id, user, if_match, body, at=body.at
    )


@router.post("/{improvement_id}/measure", response_model=ImprovementRead)
async def measure(
    improvement_id: str,
    session: SessionDep,
    response: Response,
    user: ReaderUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ImprovementRead:
    """Önce/sonra KPI ölçümü (pencere dolmadıysa 409)."""
    return await _step(session, response, svc.measure, improvement_id, user, if_match, None)


@router.post("/{improvement_id}/close", response_model=ImprovementRead)
async def close(
    improvement_id: str,
    body: ImprovementNote,
    session: SessionDep,
    response: Response,
    user: ApproverUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ImprovementRead:
    """Ölçülen iyileştirmeyi kapatır (approver / admin)."""
    return await _step(
        session, response, svc.close, improvement_id, user, if_match, body, note=body.note
    )
