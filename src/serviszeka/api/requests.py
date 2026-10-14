"""Hizmet talepleri (Faz 3): self-servis açılış, iş onayı, karşılama ve insansız çözüm ölçümü.

Roller:
- Açma: requester (kendi adına) / agent / admin (`requester` alanıyla başkası adına yalnızca
  agent/admin). Karşılama adımları her zaman `ai:fulfilment` aktörüyle araç boru hattından geçer.
- Görme: agent/approver/admin hepsini; diğerleri yalnızca kendi taleplerini ve iş onayı bekleyen
  (yöneticisi/onay grubu üyesi oldukları) talepleri (başkasınınki 404).
- İş onayı/red: talebin yöneticisi ya da onay grubu üyesi (dizinden) veya admin; yapay zekâ, talep
  eden ve talebi açan onaylayamaz.
- Düzenleme, yeniden deneme, el ile adım, ilerletme: agent (insan dokunuşu -> insansız sayılmaz).
- İptal: talep eden (kendi) ya da agent/admin.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import (
    ROLE_AGENT,
    AgentUser,
    CreatorUser,
    CurrentUser,
    IncidentViewerUser,
    ReaderUser,
    get_current_user,
)
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import ApprovalDecision, ServiceRequestState
from serviszeka.models import ServiceRequest
from serviszeka.notifications import Notifier, get_notifier, send_pending
from serviszeka.services import catalog as catalog_service
from serviszeka.services import requests as svc
from serviszeka.services.changes import same_identity
from serviszeka.services.request_schemas import (
    LinkedActionRead,
    RequestActionBody,
    RequestDecisionBody,
    ServiceRequestCreate,
    ServiceRequestRead,
    ServiceRequestUpdate,
    TouchlessMetricsRead,
)

router = APIRouter(prefix="/requests", tags=["requests"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_request_settings() -> Settings:
    """Ayar bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_request_settings)]


def get_request_llm() -> Any:
    return None


def get_request_notifier(settings: SettingsDep) -> Notifier:
    return get_notifier(settings)


LLMDep = Annotated[Any, Depends(get_request_llm)]
NotifierDep = Annotated[Notifier, Depends(get_request_notifier)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.RequestValidationError) and exc.errors:
        return HTTPException(exc.status_code, {"message": str(exc), "errors": exc.errors})
    return HTTPException(getattr(exc, "status_code", 400), str(exc))


async def _read(
    session: AsyncSession, req: ServiceRequest, user: CurrentUser
) -> ServiceRequestRead:
    actions = await svc.linked_actions(session, req.id)
    can_decide = req.state == ServiceRequestState.AWAITING_APPROVAL and await svc.can_decide(
        user, req
    )
    return ServiceRequestRead.model_validate(req).model_copy(
        update={
            "actions": [LinkedActionRead.model_validate(a) for a in actions],
            "can_decide": can_decide,
        }
    )


async def _visible(session: AsyncSession, request_id: str, user: CurrentUser) -> ServiceRequest:
    req = await svc.get_request(session, request_id)
    if req is None or not await svc.can_view(user, req):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hizmet talebi bulunamadı")
    return req


async def _commit_and_read(
    session: AsyncSession,
    req: ServiceRequest,
    user: CurrentUser,
    notifier: Notifier | None = None,
) -> ServiceRequestRead:
    await session.commit()
    await session.refresh(req)
    result = await _read(session, req, user)
    if notifier is not None:
        await send_pending(session, notifier)
    return result


@router.post("", response_model=ServiceRequestRead, status_code=status.HTTP_201_CREATED)
async def create_request(
    body: ServiceRequestCreate,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    notifier: NotifierDep,
    user: CreatorUser,
) -> ServiceRequestRead:
    """Talep açar; iş onayı gerekmiyorsa karşılama hemen başlar (R2/R3 adımlar onay bekler)."""
    if body.requester and not same_identity(body.requester, user.username):
        if not user.has_any(ROLE_AGENT):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Başkası adına talep yalnızca agent/admin açabilir."
            )
        requester = body.requester
    else:
        requester = user.username
    item = await catalog_service.get_item(session, body.item)
    if item is None or not item.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Katalog öğesi bulunamadı")
    try:
        req = await svc.create_request(
            session,
            item,
            requester=requester,
            opened_by=user,
            form_values=body.form_values,
            settings=settings,
            llm=llm,
        )
    except svc.RequestError as exc:
        raise _http(exc) from exc
    return await _commit_and_read(session, req, user, notifier)


@router.get("", response_model=list[ServiceRequestRead])
async def list_requests(
    session: SessionDep,
    user: IncidentViewerUser,
    state: ServiceRequestState | None = None,
    item: Annotated[str | None, Query(max_length=100, description="Katalog öğesi anahtarı")] = None,
    standard: bool | None = None,
    touchless: bool | None = None,
    mine: Annotated[bool, Query(description="Yalnızca talep edeni oturumdaki kullanıcı olanlar")] = False,
    awaiting_my_approval: Annotated[
        bool, Query(description="İş onayımı bekleyen talepler (yönetici / onay grubu üyesi)")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ServiceRequestRead]:
    if awaiting_my_approval:
        rows = await svc.list_requests(
            session, state=ServiceRequestState.AWAITING_APPROVAL, item_key=item, limit=500
        )
        rows = [r for r in rows if await svc.can_decide(user, r)][offset : offset + limit]
        return [await _read(session, r, user) for r in rows]
    requester = user.username if (mine or user.is_requester_only) else None
    rows = await svc.list_requests(
        session,
        requester=requester,
        state=state,
        item_key=item,
        standard=standard,
        touchless=touchless,
        limit=limit,
        offset=offset,
    )
    return [await _read(session, r, user) for r in rows]


@router.get("/metrics", response_model=TouchlessMetricsRead)
async def metrics(session: SessionDep, _user: ReaderUser) -> TouchlessMetricsRead:
    """Faz 3 kapısı: standart taleplerin insansız çözüm oranı (hedef %50)."""
    return TouchlessMetricsRead.model_validate((await svc.touchless_metrics(session)).to_dict())


@router.get("/{request_id}", response_model=ServiceRequestRead)
async def get_request(
    request_id: str, session: SessionDep, user: IncidentViewerUser
) -> ServiceRequestRead:
    return await _read(session, await _visible(session, request_id, user), user)


async def _decide(
    request_id: str,
    decision: ApprovalDecision,
    body: RequestDecisionBody | None,
    session: AsyncSession,
    settings: Settings,
    llm: Any,
    notifier: Notifier,
    user: CurrentUser,
) -> ServiceRequestRead:
    req = await _visible(session, request_id, user)
    body = body or RequestDecisionBody()
    try:
        req = await svc.decide(
            session,
            req.id,
            user,
            decision,
            body.comment,
            expected_version=body.expected_version,
            settings=settings,
            llm=llm,
        )
    except svc.RequestError as exc:
        raise _http(exc) from exc
    return await _commit_and_read(session, req, user, notifier)


@router.post("/{request_id}/approve", response_model=ServiceRequestRead)
async def approve_request(
    request_id: str,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    notifier: NotifierDep,
    user: IncidentViewerUser,
    body: RequestDecisionBody | None = None,
) -> ServiceRequestRead:
    """İş onayı (yönetici / onay grubu üyesi / admin); onayda karşılama hemen başlar."""
    return await _decide(
        request_id, ApprovalDecision.APPROVE, body, session, settings, llm, notifier, user
    )


@router.post("/{request_id}/reject", response_model=ServiceRequestRead)
async def reject_request(
    request_id: str,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    notifier: NotifierDep,
    user: IncidentViewerUser,
    body: RequestDecisionBody | None = None,
) -> ServiceRequestRead:
    return await _decide(
        request_id, ApprovalDecision.REJECT, body, session, settings, llm, notifier, user
    )


@router.post("/{request_id}/cancel", response_model=ServiceRequestRead)
async def cancel_request(
    request_id: str,
    session: SessionDep,
    user: IncidentViewerUser,
    body: RequestActionBody | None = None,
) -> ServiceRequestRead:
    req = await _visible(session, request_id, user)
    if not user.has_any(ROLE_AGENT) and not same_identity(user.username, req.requester):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Talebi yalnızca talep eden ya da agent iptal eder.")
    body = body or RequestActionBody()
    try:
        req = await svc.cancel(
            session, req.id, user, body.note, expected_version=body.expected_version
        )
    except svc.RequestError as exc:
        raise _http(exc) from exc
    return await _commit_and_read(session, req, user)


@router.patch("/{request_id}", response_model=ServiceRequestRead)
async def update_request(
    request_id: str, body: ServiceRequestUpdate, session: SessionDep, user: AgentUser
) -> ServiceRequestRead:
    """Form değerlerini düzeltir (gönderildi / iş onayı bekliyor / başarısız): insan düzenlemesi."""
    req = await _visible(session, request_id, user)
    try:
        req = await svc.update_form(
            session, req.id, user, body.form_values, expected_version=body.expected_version
        )
    except svc.RequestError as exc:
        raise _http(exc) from exc
    return await _commit_and_read(session, req, user)


@router.post("/{request_id}/retry", response_model=ServiceRequestRead)
async def retry_request(
    request_id: str,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    notifier: NotifierDep,
    user: AgentUser,
    body: RequestActionBody | None = None,
) -> ServiceRequestRead:
    """Başarısız talebi başarısız adımdan yeniden çalıştırır (insan dokunuşu)."""
    req = await _visible(session, request_id, user)
    body = body or RequestActionBody()
    try:
        req = await svc.retry(
            session, req.id, user, expected_version=body.expected_version, settings=settings,
            llm=llm,
        )
    except svc.RequestError as exc:
        raise _http(exc) from exc
    return await _commit_and_read(session, req, user, notifier)


@router.post("/{request_id}/complete-step", response_model=ServiceRequestRead)
async def complete_manual_step(
    request_id: str,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    notifier: NotifierDep,
    user: AgentUser,
    body: RequestActionBody | None = None,
) -> ServiceRequestRead:
    """Sıradaki el ile adımı tamamlandı işaretler ve karşılamayı sürdürür."""
    req = await _visible(session, request_id, user)
    body = body or RequestActionBody()
    try:
        req = await svc.complete_manual_step(
            session, req.id, user, body.note, settings=settings, llm=llm
        )
    except svc.RequestError as exc:
        raise _http(exc) from exc
    return await _commit_and_read(session, req, user, notifier)
