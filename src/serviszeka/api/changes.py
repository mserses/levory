"""Değişiklik yönetimi (Change Enablement) + CAB uç noktaları.

Okuma: agent / cab / change_manager (requester ve yalnızca approver olan kullanıcı göremez).
Oluşturma/taslak düzenleme: agent / change_manager. Onay: cab / change_manager.
Şablon ve dondurma penceresi yazma: change_manager. Durum geçişlerinde kimin neyi yapabileceği
servis katmanında (services/changes.py) denetlenir.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import llm as llm_module
from serviszeka.agents import change_risk
from serviszeka.api.cmdb import etag, impact_for, parse_if_match
from serviszeka.auth import (
    CabUser,
    ChangeCreatorUser,
    ChangeManagerUser,
    ChangeViewerUser,
    get_current_user,
)
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import ChangeState, ChangeType
from serviszeka.services import changes as svc
from serviszeka.services.change_schemas import (
    ApprovalCreate,
    ApprovalListRead,
    ApprovalRead,
    ApprovalStatusRead,
    CalendarRead,
    ChangeCreate,
    ChangePatch,
    ChangeRead,
    FreezeWindowCreate,
    FreezeWindowRead,
    ScheduleConflictRead,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
    TransitionRequest,
)
from serviszeka.services.cmdb_schemas import ImpactRead
from serviszeka.services.schemas import AIDecisionRead

router = APIRouter(prefix="/changes", tags=["changes"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_change_settings() -> Settings:
    """Ayar bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_change_settings)]


def get_change_llm() -> llm_module.LLMClient:
    """LLM bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    try:
        return llm_module.get_llm()
    except llm_module.LLMError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


LLMDep = Annotated[llm_module.LLMClient, Depends(get_change_llm)]


def _conflicts_read(conflicts: list[svc.ScheduleConflict]) -> list[ScheduleConflictRead]:
    return [ScheduleConflictRead.model_validate(c) for c in conflicts]


def _http(exc: svc.ChangeError) -> HTTPException:
    if isinstance(exc, svc.ChangeNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, svc.ChangeForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, svc.ScheduleConflictError):
        detail: Any = {
            "message": str(exc),
            "conflicts": [c.model_dump(mode="json") for c in _conflicts_read(exc.conflicts)],
        }
        return HTTPException(status.HTTP_409_CONFLICT, detail)
    if isinstance(exc, (svc.VersionConflictError, svc.ApprovalError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


async def _get_or_404(session: AsyncSession, change_id: str):
    change = await svc.get_change(session, change_id)
    if change is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Değişiklik bulunamadı")
    return change


async def _read(session: AsyncSession, change) -> ChangeRead:
    # Satır içi ilişkiler (affected_cis) taze okunur; expire_on_commit=False
    await session.refresh(change, attribute_names=["affected_cis"])
    return ChangeRead.model_validate(change)


# --- liste / takvim / şablon / dondurma (/{change_id}'den önce tanımlanmalı) ---------------


@router.get("", response_model=list[ChangeRead])
async def list_changes(
    session: SessionDep,
    _user: ChangeViewerUser,
    state: ChangeState | None = None,
    change_type: ChangeType | None = None,
    ci_id: str | None = None,
    mine: Annotated[bool, Query(description="Yalnızca oturumdaki kullanıcının talepleri")] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ChangeRead]:
    rows = await svc.list_changes(
        session,
        state=state,
        change_type=change_type,
        ci_id=ci_id,
        requested_by=_user.username if mine else None,
        limit=limit,
        offset=offset,
    )
    return [ChangeRead.model_validate(r) for r in rows]


@router.post("", response_model=ChangeRead, status_code=status.HTTP_201_CREATED)
async def create_change(
    body: ChangeCreate, session: SessionDep, response: Response, user: ChangeCreatorUser
) -> ChangeRead:
    try:
        change = await svc.create_change(session, body, user)
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    response.headers["ETag"] = etag(change.version)
    return await _read(session, change)


@router.get("/calendar", response_model=CalendarRead)
async def change_calendar(
    session: SessionDep,
    _user: ChangeViewerUser,
    from_: Annotated[AwareDatetime, Query(alias="from")],
    to: AwareDatetime,
) -> CalendarRead:
    """[from, to) aralığına değen değişiklikler (taslak/iptal/red hariç) + dondurma pencereleri."""
    try:
        cal = await svc.calendar(session, from_, to)
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    return CalendarRead(
        changes=[ChangeRead.model_validate(c) for c in cal.changes],
        freeze_windows=[FreezeWindowRead.model_validate(f) for f in cal.freeze_windows],
    )


@router.get("/templates", response_model=list[TemplateRead])
async def list_templates(
    session: SessionDep, _user: ChangeViewerUser, active: bool | None = None
) -> list[TemplateRead]:
    return [TemplateRead.model_validate(t) for t in await svc.list_templates(session, active=active)]


@router.post("/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreate, session: SessionDep, user: ChangeManagerUser
) -> TemplateRead:
    try:
        template = await svc.create_template(session, body, user)
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    return TemplateRead.model_validate(template)


@router.patch("/templates/{template_id}", response_model=TemplateRead)
async def update_template(
    template_id: str, body: TemplateUpdate, session: SessionDep, user: ChangeManagerUser
) -> TemplateRead:
    try:
        template = await svc.update_template(session, template_id, body, user)
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    return TemplateRead.model_validate(template)


@router.get("/freeze-windows", response_model=list[FreezeWindowRead])
async def list_freeze_windows(
    session: SessionDep,
    _user: ChangeViewerUser,
    from_: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    to: AwareDatetime | None = None,
) -> list[FreezeWindowRead]:
    rows = await svc.list_freeze_windows(session, start=from_, end=to)
    return [FreezeWindowRead.model_validate(r) for r in rows]


@router.post("/freeze-windows", response_model=FreezeWindowRead, status_code=status.HTTP_201_CREATED)
async def create_freeze_window(
    body: FreezeWindowCreate, session: SessionDep, user: ChangeManagerUser
) -> FreezeWindowRead:
    fw = await svc.create_freeze_window(session, body, user)
    await session.commit()
    return FreezeWindowRead.model_validate(fw)


@router.delete("/freeze-windows/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_freeze_window(
    window_id: str, session: SessionDep, user: ChangeManagerUser
) -> Response:
    try:
        await svc.delete_freeze_window(session, window_id, user)
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/decisions/{decision_id}/approve", response_model=AIDecisionRead)
async def approve_decision(
    decision_id: str, session: SessionDep, user: ChangeManagerUser
) -> AIDecisionRead:
    """Yapay zekâ risk önerisini onaylar (yalnızca risk alanları uygulanır, durum değişmez)."""
    try:
        decision = await svc.approve_ai_decision(session, decision_id, user)
    except svc.StaleDecisionError as exc:
        await session.commit()  # red denetim kaydı kalıcı olmalı
        raise _http(exc) from exc
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    return AIDecisionRead.model_validate(decision)


# --- tekil değişiklik ---------------------------------------------------------------------


@router.get("/{change_id}", response_model=ChangeRead)
async def get_change(
    change_id: str, session: SessionDep, response: Response, _user: ChangeViewerUser
) -> ChangeRead:
    change = await _get_or_404(session, change_id)
    response.headers["ETag"] = etag(change.version)
    return ChangeRead.model_validate(change)


@router.patch("/{change_id}", response_model=ChangeRead)
async def update_change(
    change_id: str,
    body: ChangePatch,
    session: SessionDep,
    response: Response,
    user: ChangeCreatorUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ChangeRead:
    expected = parse_if_match(if_match)
    if expected is None:
        expected = body.expected_version
    if user.is_ai and expected is None:
        # Yapay zekâ sürümsüz yazamaz (servis yapay zekâ düzenlemesini zaten reddeder: 403)
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            'Yapay zekâ yazmasında If-Match başlığı (örn. "3") ya da expected_version zorunludur.',
        )
    try:
        change = await svc.update_change(session, change_id, body, user, expected_version=expected)
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    response.headers["ETag"] = etag(change.version)
    return await _read(session, change)


@router.post("/{change_id}/transition", response_model=ChangeRead)
async def transition(
    change_id: str,
    body: TransitionRequest,
    session: SessionDep,
    response: Response,
    settings: SettingsDep,
    user: ChangeViewerUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ChangeRead:
    """Durum geçişi. Kim hangi geçişi yapabilir, servis katmanı denetler (403)."""
    expected = parse_if_match(if_match)
    if expected is None:
        expected = body.expected_version
    try:
        change = await svc.transition(
            session,
            change_id,
            body.to_state,
            user,
            expected_version=expected,
            notes=body.notes,
            review_notes=body.review_notes,
            settings=settings,
        )
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    response.headers["ETag"] = etag(change.version)
    return await _read(session, change)


@router.get("/{change_id}/approvals", response_model=ApprovalListRead)
async def list_approvals(
    change_id: str, session: SessionDep, settings: SettingsDep, _user: ChangeViewerUser
) -> ApprovalListRead:
    await _get_or_404(session, change_id)
    items = await svc.list_approvals(session, change_id)
    state = await svc.approval_status(session, change_id, settings=settings)
    return ApprovalListRead(
        status=ApprovalStatusRead.model_validate(state),
        items=[ApprovalRead.model_validate(a) for a in items],
    )


@router.post(
    "/{change_id}/approvals", response_model=ApprovalRead, status_code=status.HTTP_201_CREATED
)
async def create_approval(
    change_id: str,
    body: ApprovalCreate,
    session: SessionDep,
    settings: SettingsDep,
    user: CabUser,
) -> ApprovalRead:
    # Onaylayan her zaman belirteçteki kullanıcıdır; gövde yalnızca doğrulama içindir
    if body.approver and body.approver != user.username:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Başkası adına onay verilemez")
    try:
        approval = await svc.record_approval(
            session, change_id, user, body.decision, body.comment, settings=settings
        )
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    return ApprovalRead.model_validate(approval)


@router.get("/{change_id}/conflicts", response_model=list[ScheduleConflictRead])
async def conflicts(
    change_id: str, session: SessionDep, _user: ChangeViewerUser
) -> list[ScheduleConflictRead]:
    """Planlanan penceredeki dondurma ve aynı-CI çakışmaları (zamanlama öncesi kontrol)."""
    try:
        return _conflicts_read(await svc.find_conflicts(session, change_id))
    except svc.ChangeError as exc:
        raise _http(exc) from exc


@router.get("/{change_id}/impact", response_model=ImpactRead)
async def change_impact(
    change_id: str,
    session: SessionDep,
    _user: ChangeViewerUser,
    max_depth: Annotated[int, Query(ge=1, le=20)] = 5,
) -> ImpactRead:
    """Etkilenen CI'ların CMDB etki analizi (CAB değerlendirmesi için)."""
    change = await _get_or_404(session, change_id)
    if not change.affected_ci_ids:
        return ImpactRead(root_ci_ids=[], affected=[], highest_criticality=None, business_services=[])
    return await impact_for(session, change.affected_ci_ids, max_depth)


@router.get("/{change_id}/decisions", response_model=list[AIDecisionRead])
async def list_decisions(
    change_id: str, session: SessionDep, _user: ChangeViewerUser
) -> list[AIDecisionRead]:
    await _get_or_404(session, change_id)
    return [AIDecisionRead.model_validate(d) for d in await svc.list_decisions(session, change_id)]



@router.post("/{change_id}/assess", response_model=AIDecisionRead)
async def assess_risk(
    change_id: str,
    session: SessionDep,
    settings: SettingsDep,
    llm: LLMDep,
    _user: ChangeCreatorUser,
) -> AIDecisionRead:
    """Yapay zekâ risk değerlendirmesi (agents/change_risk). Yalnızca risk alanları, politika izin
    verirse yazılır; durum geçişi ve onay asla yapılmaz. Riski düşüren öneri change_manager onayı
    bekler (`POST /changes/decisions/{id}/approve`)."""
    change = await _get_or_404(session, change_id)
    try:
        decision = await change_risk.assess(session, change, llm, settings)
    except svc.ChangeError as exc:
        raise _http(exc) from exc
    await session.commit()
    return AIDecisionRead.model_validate(decision)
